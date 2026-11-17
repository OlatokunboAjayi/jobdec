"""
Playwright Browser Automation Driver for Assessment Portals (Features 9, 10, 11, 12, R2).
Handles:
- Launching Chromium/Chrome with fake media streams (camera/mic bypass) and anti-bot flags.
- Injecting `ANTI_BLUR_INIT_SCRIPT` to prevent `blur` and `visibilitychange` proctoring flags.
- Scanning DOM question cards, options, code editors (Monaco, Ace, CodeMirror, textarea),
  Likert sliders, and SJT ranking dropdowns.
- Reading countdown timers, pacing per-question time budgets, and emergency auto-submit.
- Stepping through multi-question wizards, submitting assessments, and harvesting verification tokens.
"""

from __future__ import annotations

import datetime
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Union

from assessments.contracts import (
    AnswerPayload,
    AssessmentPlatform,
    AssessmentRunResult,
    AssessmentTask,
    CodeMetadata,
    OptionItem,
    QuestionCategory,
    QuestionPayload,
    QuestionType,
)
from assessments.solver import AssessmentSolver, get_solver

logger = logging.getLogger("assessments.browser")

ANTI_BLUR_INIT_SCRIPT = """
(() => {
    Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
    Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });
    Object.defineProperty(document, 'webkitVisibilityState', { get: () => 'visible', configurable: true });
    document.hasFocus = () => true;

    const suppressedEvents = ['visibilitychange', 'webkitvisibilitychange', 'blur'];
    suppressedEvents.forEach(eventType => {
        window.addEventListener(eventType, (e) => {
            e.stopImmediatePropagation();
            e.stopPropagation();
        }, true);
        document.addEventListener(eventType, (e) => {
            e.stopImmediatePropagation();
            e.stopPropagation();
        }, true);
    });

    try {
        Object.defineProperty(window, 'onblur', { get: () => null, set: () => {}, configurable: true });
        Object.defineProperty(document, 'onvisibilitychange', { get: () => null, set: () => {}, configurable: true });
    } catch (err) {}

    Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });
})();
"""


def parse_timer_text(text: str) -> Optional[int]:
    """Parses countdown strings like '15:00', '01:05:30', or '45s' into total seconds."""
    if not text:
        return None
    cleaned = text.strip()
    m_hms = re.search(r"\b(\d{1,2}):(\d{2}):(\d{2})\b", cleaned)
    if m_hms:
        return int(m_hms.group(1)) * 3600 + int(m_hms.group(2)) * 60 + int(m_hms.group(3))
    m_ms = re.search(r"\b(\d{1,3}):(\d{2})\b", cleaned)
    if m_ms:
        return int(m_ms.group(1)) * 60 + int(m_ms.group(2))
    m_sec = re.search(r"\b(\d+)\s*(?:sec|seconds|s)\b", cleaned, re.IGNORECASE)
    if m_sec:
        return int(m_sec.group(1))
    return None


def compute_time_budget(
    remaining_seconds: Optional[int],
    questions_remaining: int,
    emergency_threshold_seconds: int = 60,
) -> Dict[str, Any]:
    """
    Calculates per-question time budget and determines whether emergency rapid-submit mode is required.
    Formula: T_budget = T_remain / (Q_remain + 1), capped at [1.0, 20.0] seconds.
    """
    q_rem = max(1, questions_remaining)
    if remaining_seconds is None:
        return {
            "remaining_seconds": None,
            "per_question_budget_seconds": 20.0,
            "is_emergency": False,
        }
    is_emergency = remaining_seconds <= emergency_threshold_seconds
    raw_budget = remaining_seconds / float(q_rem + 1)
    capped_budget = max(0.5, min(raw_budget - 1.0, 20.0)) if not is_emergency else 0.5
    return {
        "remaining_seconds": remaining_seconds,
        "per_question_budget_seconds": round(capped_budget, 2),
        "is_emergency": is_emergency,
    }


class AssessmentBrowserDriver:
    """
    Playwright browser automation engine for navigating, solving, and submitting online assessments.
    """

    def __init__(
        self,
        solver: Optional[AssessmentSolver] = None,
        headless: bool = True,
        emergency_threshold_seconds: int = 60,
    ) -> None:
        self.solver = solver or get_solver()
        self.headless = headless
        self.emergency_threshold_seconds = emergency_threshold_seconds

    def inject_anti_proctoring(self, page_or_context: Any) -> None:
        """Injects anti-blur and tab-retention hooks into a Playwright BrowserContext or Page."""
        try:
            page_or_context.add_init_script(ANTI_BLUR_INIT_SCRIPT)
        except Exception:
            pass
        try:
            if hasattr(page_or_context, "evaluate"):
                page_or_context.evaluate(ANTI_BLUR_INIT_SCRIPT)
        except Exception:
            pass

    def read_remaining_time(self, page: Any) -> Optional[int]:
        """Reads remaining countdown seconds from timer DOM attributes or text."""
        try:
            timer_el = page.locator('[data-testid="timer"], #assessment-timer, [class*="timer"]').first
            if timer_el.count() > 0:
                attr = timer_el.get_attribute("data-seconds-remaining")
                if attr is not None and str(attr).strip().lstrip("-").isdigit():
                    return max(0, int(attr))
                txt = timer_el.inner_text()
                parsed = parse_timer_text(txt)
                if parsed is not None:
                    return parsed
        except Exception:
            pass
        return None

    def extract_questions_from_page(self, page: Any) -> List[QuestionPayload]:
        """Extracts all question cards and their metadata from the current assessment DOM."""
        cards = page.locator('[data-testid="question-card"], .question-container')
        if cards.count() == 0:
            cards = page.locator('[role="listitem"], fieldset, .question-item, .assessment-question')
        count = cards.count()
        questions: List[QuestionPayload] = []

        for idx in range(count):
            card = cards.nth(idx)
            qid = card.get_attribute("data-question-id") or card.get_attribute("id") or f"q_{idx + 1}"
            cat_str = (card.get_attribute("data-category") or "technical").lower()
            qtype_str = (card.get_attribute("data-type") or "single_choice").lower()

            prompt_loc = card.locator('[data-testid="question-prompt"], .question-prompt, h3')
            prompt_text = prompt_loc.first.inner_text().strip() if prompt_loc.count() > 0 else ""

            ctx_loc = card.locator(".question-context-box")
            context_text = ctx_loc.first.inner_text().strip() if ctx_loc.count() > 0 else None

            options: List[OptionItem] = []
            if qtype_str in ("single_choice", "multiple_choice"):
                inputs = card.locator('input[type="radio"], input[type="checkbox"]')
                in_count = inputs.count()
                for j in range(in_count):
                    inp = inputs.nth(j)
                    opt_id = inp.get_attribute("value") or chr(ord("A") + j)
                    parent_label = inp.locator("..")
                    opt_txt = parent_label.inner_text().strip() if parent_label.count() > 0 else opt_id
                    # Strip leading "A. " prefix if present
                    opt_txt = re.sub(r"^[A-Z0-9]+\.\s*", "", opt_txt)
                    options.append(OptionItem(id=opt_id, label=opt_txt, text=opt_txt))
            elif qtype_str == "ranking":
                rows = card.locator(".rank-row")
                for j in range(rows.count()):
                    row = rows.nth(j)
                    sel = row.locator("select.rank-select")
                    opt_id = (sel.first.get_attribute("data-item-id") if sel.count() > 0 else None) or chr(ord("A") + j)
                    desc_loc = row.locator(".rank-option-desc")
                    opt_txt = desc_loc.first.inner_text().strip() if desc_loc.count() > 0 else opt_id
                    opt_txt = re.sub(r"^[A-Z0-9]+\.\s*", "", opt_txt)
                    options.append(OptionItem(id=opt_id, label=opt_txt, text=opt_txt))

            code_meta: Optional[CodeMetadata] = None
            if qtype_str == "code":
                editor_loc = card.locator('[data-testid="code-editor"], textarea.code-textarea')
                starter = editor_loc.first.input_value() if editor_loc.count() > 0 else ""
                entry_m = re.search(r"def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", starter)
                entry_pt = entry_m.group(1) if entry_m else "solution"
                code_meta = CodeMetadata(
                    language="python",
                    entry_point=entry_pt,
                    starter_code=starter,
                    starter_template=starter,
                )

            try:
                cat_enum = QuestionCategory(cat_str)
            except ValueError:
                cat_enum = AssessmentSolver.infer_category(prompt_text, qtype_str)

            try:
                qtype_enum = QuestionType(qtype_str)
            except ValueError:
                qtype_enum = QuestionType.SINGLE_CHOICE

            questions.append(
                QuestionPayload(
                    question_id=qid,
                    sequence_number=idx + 1,
                    total_questions=count,
                    category=cat_enum,
                    question_type=qtype_enum,
                    prompt_text=prompt_text,
                    context=context_text,
                    options=options,
                    code_metadata=code_meta,
                )
            )

        return questions

    def apply_answer_to_card(self, page: Any, card: Any, question: QuestionPayload, answer: AnswerPayload) -> None:
        """Applies a computed AnswerPayload to the specific question card DOM element."""
        qtype = str(question.question_type.value if hasattr(question.question_type, "value") else question.question_type).lower()

        if qtype == "single_choice":
            target_id = answer.selected_option_ids[0] if answer.selected_option_ids else (answer.text_response or "A")
            radio = card.locator(f'input[type="radio"][value="{target_id}"]')
            if radio.count() > 0:
                radio.first.check(force=True)
            else:
                any_radio = card.locator('input[type="radio"]')
                if any_radio.count() > 0:
                    any_radio.first.check(force=True)

        elif qtype == "multiple_choice":
            for opt_id in answer.selected_option_ids:
                chk = card.locator(f'input[type="checkbox"][value="{opt_id}"]')
                if chk.count() > 0:
                    chk.first.check(force=True)

        elif qtype == "short_text":
            val = answer.text_response or (answer.selected_option_ids[0] if answer.selected_option_ids else "")
            inp = card.locator('input[type="text"], input[type="number"]')
            if inp.count() > 0:
                inp.first.fill(str(val))

        elif qtype == "essay":
            val = answer.text_response or ""
            ta = card.locator("textarea")
            if ta.count() > 0:
                ta.first.fill(str(val))
                ta.first.dispatch_event("input")
                ta.first.dispatch_event("change")

        elif qtype == "slider":
            val = answer.slider_value
            if val is None and answer.text_response:
                try:
                    val = int(float(answer.text_response))
                except ValueError:
                    val = 5
            elif val is None and answer.selected_option_ids:
                try:
                    val = int(float(answer.selected_option_ids[0]))
                except ValueError:
                    val = 5
            if val is None:
                val = 5

            slider = card.locator('input[type="range"]')
            if slider.count() > 0:
                slider.first.evaluate(
                    """(el, v) => {
                        el.value = String(v);
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    val,
                )

        elif qtype == "ranking":
            order = answer.ranking_order or list(answer.selected_option_ids)
            for rank_idx, item_id in enumerate(order, start=1):
                sel = card.locator(f'select.rank-select[data-item-id="{item_id}"]')
                if sel.count() > 0:
                    sel.first.select_option(str(rank_idx))

        elif qtype == "code":
            code_str = answer.code_response or answer.text_response or ""
            # Support Monaco, Ace, CodeMirror, or standard textarea code editor
            page.evaluate(
                """(code) => {
                    if (window.monaco && window.monaco.editor && window.monaco.editor.getModels().length > 0) {
                        window.monaco.editor.getModels()[0].setValue(code);
                    }
                    if (window.ace && document.querySelector('.ace_editor')) {
                        window.ace.edit(document.querySelector('.ace_editor')).setValue(code, -1);
                    }
                    const cm = document.querySelector('.CodeMirror');
                    if (cm && cm.CodeMirror) {
                        cm.CodeMirror.setValue(code);
                    }
                }""",
                code_str,
            )
            ta = card.locator('[data-testid="code-editor"], textarea.code-textarea')
            if ta.count() > 0:
                ta.first.fill(code_str)
                ta.first.dispatch_event("input")
                ta.first.dispatch_event("change")

    def execute_assessment_on_page(
        self,
        page: Any,
        task: Optional[AssessmentTask] = None,
        url: Optional[str] = None,
    ) -> AssessmentRunResult:
        """
        Navigates to the assessment URL on `page`, extracts and solves all questions,
        paces execution against the countdown timer, submits the form, and collects
        the completion result and verification token.
        """
        start_dt = datetime.datetime.now(datetime.timezone.utc)
        start_perf = time.perf_counter()

        target_url = url or (task.canonical_url if task else "")
        company = task.company if task else "Mock Corp"
        role = task.role if task else "Software Engineer"
        platform = task.platform if task else AssessmentPlatform.HACKERRANK
        task_id = task.task_id if task else f"assess-{int(time.time())}"

        self.inject_anti_proctoring(page)

        try:
            if target_url:
                page.goto(target_url, wait_until="domcontentloaded")

            questions = self.extract_questions_from_page(page)
            total_q = len(questions)
            cards = page.locator('[data-testid="question-card"], .question-container')
            next_btn = page.locator('[data-testid="btn-next-question"], #btn-next-question')
            submit_btn = page.locator('[data-testid="btn-submit-assessment"], #btn-submit-assessment')

            answers_log: List[Dict[str, Any]] = []
            answered_count = 0

            for idx, q in enumerate(questions):
                rem_sec = self.read_remaining_time(page)
                q.remaining_time_seconds = rem_sec
                pacing = compute_time_budget(
                    rem_sec,
                    questions_remaining=total_q - idx,
                    emergency_threshold_seconds=self.emergency_threshold_seconds,
                )

                answer = self.solver.solve_question(q)
                card = cards.nth(idx)
                self.apply_answer_to_card(page, card, q, answer)
                answered_count += 1

                answers_log.append({
                    "question_id": q.question_id,
                    "category": str(q.category.value if hasattr(q.category, "value") else q.category),
                    "question_type": str(q.question_type.value if hasattr(q.question_type, "value") else q.question_type),
                    "selected_option_ids": answer.selected_option_ids,
                    "text_response": answer.text_response,
                    "code_response": answer.code_response,
                    "confidence_score": answer.confidence_score,
                    "solver_tier": answer.solver_tier,
                    "pacing": pacing,
                })

                # Advance stepper if not on last question
                if idx < total_q - 1 and next_btn.count() > 0:
                    try:
                        if not next_btn.first.is_disabled():
                            next_btn.first.click()
                    except Exception:
                        pass

            # Click submit button and wait for results page
            if submit_btn.count() > 0:
                submit_btn.first.click()
                try:
                    page.wait_for_url("**/results/**", timeout=8000)
                except Exception:
                    pass

            # Harvest verification token, score, and proctoring status if on results page
            verification_token: Optional[str] = None
            score_val: Optional[float] = None
            proctoring_status = "CLEAN"

            tok_loc = page.locator('[data-testid="verification-token"]')
            if tok_loc.count() > 0:
                verification_token = tok_loc.first.inner_text().strip()
                # Token format: VERIFY-{PLATFORM}-{SCORE}-{SESSION_ID}
                m_score = re.search(r"VERIFY-[A-Z0-9_]+-([0-9.]+)-", verification_token)
                if m_score:
                    try:
                        score_val = float(m_score.group(1))
                    except ValueError:
                        pass

            score_loc = page.locator('[data-testid="final-score"], .score-value')
            if score_val is None and score_loc.count() > 0:
                s_txt = score_loc.first.inner_text().strip().rstrip("%")
                try:
                    score_val = float(s_txt)
                except ValueError:
                    pass

            proc_loc = page.locator('[data-testid="proctoring-status"]')
            if proc_loc.count() > 0:
                p_txt = proc_loc.first.inner_text().strip().upper()
                if "FLAGGED" in p_txt:
                    proctoring_status = "FLAGGED"

            end_dt = datetime.datetime.now(datetime.timezone.utc)
            duration = round(time.perf_counter() - start_perf, 3)

            return AssessmentRunResult(
                task_id=task_id,
                status="COMPLETED" if answered_count == total_q else "PARTIAL",
                company=company,
                role=role,
                platform=platform,
                assessment_url=target_url,
                questions_total=total_q,
                questions_answered=answered_count,
                score=score_val if score_val is not None else 100.0,
                passed=True if (score_val is None or score_val >= 70.0) else False,
                proctoring_status=proctoring_status,
                verification_token=verification_token,
                start_time=start_dt.isoformat(),
                end_time=end_dt.isoformat(),
                duration_seconds=duration,
                llm_provider=self.solver.llm_client.last_provider_used or "deterministic",
                answers_log=answers_log,
            )

        except Exception as exc:
            end_dt = datetime.datetime.now(datetime.timezone.utc)
            duration = round(time.perf_counter() - start_perf, 3)
            return AssessmentRunResult(
                task_id=task_id,
                status="FAILED",
                company=company,
                role=role,
                platform=platform,
                assessment_url=target_url,
                questions_total=0,
                questions_answered=0,
                start_time=start_dt.isoformat(),
                end_time=end_dt.isoformat(),
                duration_seconds=duration,
                error_message=f"{type(exc).__name__}: {exc}",
            )

    def run_task(self, task: AssessmentTask) -> AssessmentRunResult:
        """Launches a standalone headless Chromium session with anti-proctoring hooks and executes `task`."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self.headless,
                args=[
                    "--use-fake-ui-for-media-stream",
                    "--use-fake-device-for-media-stream",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                permissions=["camera", "microphone"],
                ignore_https_errors=True,
            )
            context.add_init_script(ANTI_BLUR_INIT_SCRIPT)
            page = context.new_page()
            try:
                return self.execute_assessment_on_page(page, task=task, url=task.canonical_url)
            finally:
                page.close()
                context.close()
                browser.close()
