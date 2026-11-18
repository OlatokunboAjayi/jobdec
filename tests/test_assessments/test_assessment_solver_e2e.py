"""
Comprehensive Unit & End-to-End Integration Tests for the Autonomous Assessment Solver (R1-R4).
Verifies:
- Multi-platform URL extraction, redirect unwrapping, applications.csv & inbox.json ingestion
- All 31 ground-truth fixtures across Behavioral, Technical, Mathematics, and Logic domains
  graded by the Mock Portal Grader (both with StubLLMProvider and pure deterministic domain solvers)
- Dual-Provider LLM failover (Claude 429/401 -> OpenRouter -> deterministic fallback) & budget caps
- Configurable .env behavioral templates & Likert consistency tracking
- Full Playwright browser automation across all 5 simulated assessment platforms
  (HackerRank, Codility, Criteria CCAT, TestGorilla, HireVue) with anti-proctoring verification
- Structured JSONL audit logging, applications.csv stage progression sync, and CLI entry points
"""

from __future__ import annotations

import csv
import json
import os
import pathlib
import pytest

from assessments import (
    AssessmentBrowserDriver,
    AssessmentNotifier,
    AssessmentOrchestrator,
    AssessmentPlatform,
    AssessmentRunResult,
    AssessmentSolver,
    AssessmentTask,
    BudgetExceededError,
    DualProviderLLMClient,
    QuestionCategory,
    QuestionPayload,
    QuestionType,
    classify_platform,
    compute_time_budget,
    dispatch_completion,
    extract_assessment_tasks,
    extract_urls_from_text_or_html,
    get_behavioral_questions_catalog,
    ingest_from_applications_csv,
    ingest_from_inbox_cache,
    load_behavioral_config,
    orchestrator_main,
    parse_timer_text,
    solve_question,
    unwrap_redirect_url,
)
from tests.mock_portal.grader import Grader
from tests.mock_portal.stub_provider import StubLLMProvider


# =============================================================================
# 1. Ingestion, Redirect Unwrapping & Platform Classification Tests (R2)
# =============================================================================

def test_unwrap_redirect_urls():
    """Verifies SafeLinks, Google URL redirects, and SendGrid tracking links unwrap cleanly."""
    target = "https://www.hackerrank.com/tests/abc123xyz"
    safelink = f"https://nam02.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.hackerrank.com%2Ftests%2Fabc123xyz&data=05"
    google_redir = f"https://www.google.com/url?q=https%3A%2F%2Fwww.hackerrank.com%2Ftests%2Fabc123xyz&sa=D"
    sendgrid_redir = f"https://u12345.ct.sendgrid.net/ls/click?url=https%3A%2F%2Fwww.hackerrank.com%2Ftests%2Fabc123xyz"

    assert unwrap_redirect_url(safelink) == target
    assert unwrap_redirect_url(google_redir) == target
    assert unwrap_redirect_url(sendgrid_redir) == target


@pytest.mark.parametrize(
    "url,expected_platform",
    [
        ("https://www.hackerrank.com/tests/swe2027", AssessmentPlatform.HACKERRANK),
        ("https://hr.gs/abcd12", AssessmentPlatform.HACKERRANK),
        ("https://app.codility.com/c/run/ABC-123/", AssessmentPlatform.CODILITY),
        ("https://app.codesignal.com/public-test/xyz987", AssessmentPlatform.CODESIGNAL),
        ("https://app.testgorilla.com/test/tg555", AssessmentPlatform.TESTGORILLA),
        ("https://www.ondemandassessment.com/link/index/CCAT01", AssessmentPlatform.CRITERIA_CCAT),
        ("https://blackrock.hirevue.com/interview/hv999", AssessmentPlatform.HIREVUE),
        ("https://app.pymetrics.com/candidate/pym111", AssessmentPlatform.PYMETRICS),
        ("https://talentcentral.us.shl.com/player/assessment/123", AssessmentPlatform.SHL_TALENTCENTRAL),
        ("https://docs.google.com/forms/d/e/1FAIpQLSe/viewform", AssessmentPlatform.GOOGLE_FORMS),
        ("https://company.typeform.com/to/tf888", AssessmentPlatform.TYPEFORM),
        ("https://forms.cloud.microsoft/r/1FFQ0aXEAB", AssessmentPlatform.MICROSOFT_FORMS),
        ("https://gm.wd5.myworkdayjobs.com/en-US/Careers/assessment/123", AssessmentPlatform.WORKDAY),
        ("http://127.0.0.1:8888/portal/hackerrank", AssessmentPlatform.HACKERRANK),
        ("http://127.0.0.1:8888/portal/ccat", AssessmentPlatform.CRITERIA_CCAT),
    ],
)
def test_classify_platform_all_12_plus_platforms(url, expected_platform):
    """Verifies platform recognition across all 12+ assessment providers and local mock portal."""
    assert classify_platform(url) == expected_platform


def test_extract_urls_from_html_email():
    """Verifies HTML email anchor parsing prioritizes assessment links and skips unsubscribe links."""
    html_email = """
    <html>
      <body>
        <p>You have been invited to complete the technical assessment for Optiver.</p>
        <a href="https://company.com/unsubscribe">Unsubscribe</a>
        <a href="https://nam01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fapp.codility.com%2Fc%2Frun%2FOPTIVER-2027">Start Assessment</a>
      </body>
    </html>
    """
    urls = extract_urls_from_text_or_html(html_email)
    assert len(urls) == 1
    assert urls[0] == "https://app.codility.com/c/run/OPTIVER-2027"
    tasks = extract_assessment_tasks(html_email)
    assert len(tasks) == 1
    assert tasks[0].platform == AssessmentPlatform.CODILITY


def test_ingest_real_applications_csv_and_inbox_cache(tmp_path):
    """Verifies ingestion of pending assessment rows from applications.csv and inbox.json."""
    csv_file = tmp_path / "applications.csv"
    csv_file.write_text(
        "ID,Company,Role,Stage,Job URL,Next Action,Notes\n"
        "1,AcmeCorp,Software Engineer Intern,OA/Assessment,https://app.codility.com/c/run/ACME-123,Complete assessment,\n"
        "2,BetaQuant,Trading Intern,OA/Assessment,https://app.hackerrank.com/tests/beta-456,Take test,\n",
        encoding="utf-8",
    )
    csv_tasks = ingest_from_applications_csv(csv_path=csv_file)
    assert len(csv_tasks) == 2
    assert {t.company for t in csv_tasks} == {"AcmeCorp", "BetaQuant"}

    inbox_file = tmp_path / "inbox.json"
    inbox_file.write_text(
        json.dumps({
            "101": {
                "company": "GammaAI",
                "role": "FDE Intern",
                "kind": "assessment",
                "subject": "Online Assessment Invitation",
                "body": "Please take your CodeSignal test at https://app.codesignal.com/public-test/gamma-999",
            }
        }),
        encoding="utf-8",
    )
    inbox_tasks = ingest_from_inbox_cache(inbox_path=inbox_file)
    assert len(inbox_tasks) == 1
    assert inbox_tasks[0].platform == AssessmentPlatform.CODESIGNAL

    combined = extract_assessment_tasks(csv_file)
    assert len(combined) == 2


# =============================================================================
# 2. Multi-Modal Solving Engine Verification Across All 31 Fixtures (R3)
# =============================================================================

def test_deterministic_domain_solvers_pass_all_31_fixtures():
    """
    Verifies that our 4 domain solvers (Behavioral, Technical, Mathematics, Logic)
    solve all 31 mock portal fixtures with 100% pass rate even WITHOUT StubLLMProvider!
    """
    grader = Grader()
    assert len(grader.fixtures) == 31

    # Instantiate solver with no stub provider to force pure domain solver execution
    llm_client = DualProviderLLMClient(provider_preference="claude", stub_provider=None)
    solver = AssessmentSolver(llm_client=llm_client, prefer_llm=False)

    for qid, q_model in grader.fixtures.items():
        q_payload = QuestionPayload(
            question_id=q_model.question_id,
            category=QuestionCategory(q_model.category),
            question_type=QuestionType(q_model.question_type),
            prompt_text=q_model.prompt,
            context=q_model.context,
            options=[{"id": o.id, "text": o.text} for o in (q_model.options or [])],
            code_metadata=q_model.code_metadata.model_dump() if q_model.code_metadata else None,
            input_specifications=q_model.input_specifications,
            rubric=q_model.rubric,
        )
        ans = solver.solve_question(q_payload)
        sub_item = ans.to_portal_submission_item(q_payload.question_type)
        grade_detail = grader.grade_question(qid, sub_item)
        assert grade_detail.passed is True, (
            f"Domain solver failed on {qid} ({q_model.category}/{q_model.question_type}): "
            f"score={grade_detail.score}, feedback={grade_detail.feedback}, sub_item={sub_item}"
        )
        assert grade_detail.score >= 85.0


def test_behavioral_env_template_override_and_likert_consistency(monkeypatch):
    """Verifies that custom behavioral rules in .env dynamically guide behavioral responses."""
    custom_story = (
        "At Delta Air Lines, I architected a custom Python automation pipeline that validated "
        "3,000+ records and processed 50,000+ employee shift bids, saving 150+ hours with 100% validation accuracy. "
        "This custom .env rule confirms dynamic behavioral template loading across enterprise operations."
    )
    monkeypatch.setenv("ASSESSMENT_TEMPLATE_AMBIGUITY", custom_story)
    cfg = load_behavioral_config()
    assert cfg.star_templates["ambiguity_process_automation"] == custom_story

    ans = solve_question({
        "question_id": "custom-beh-1",
        "category": "behavioral",
        "question_type": "essay",
        "prompt_text": "Describe a situation where you had to automate a process under ambiguous requirements.",
    })
    assert "custom .env rule confirms dynamic behavioral template loading" in (ans.text_response or "")

    catalog = get_behavioral_questions_catalog()
    assert len(catalog) >= 4
    assert any(item["env_key"] == "ASSESSMENT_TEMPLATE_AMBIGUITY" for item in catalog)


def test_dual_provider_failover_claude_to_openrouter(monkeypatch):
    """Verifies automatic failover from Claude (HTTP 429) to OpenRouter and budget enforcement."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock_anthropic")
    monkeypatch.setenv("OPENROUTER_API_KEY", "mock_openrouter")
    stub = StubLLMProvider()
    client = DualProviderLLMClient(
        provider_preference="auto",
        fallback_enabled=True,
        daily_budget_usd=10.0,
        stub_provider=stub,
        max_retries=1,
    )

    with stub.simulate_rate_limit(provider="claude", count=5):
        res = client.solve_with_failover({"question_id": "tech-code-two-sum"})
        assert res["confidence_score"] == 1.0
        assert client.last_provider_used == "openrouter"

    # Budget exceeded test
    client_zero = DualProviderLLMClient(daily_budget_usd=0.0, stub_provider=stub)
    with pytest.raises(BudgetExceededError):
        client_zero.solve_with_failover({"question_id": "tech-code-two-sum"})


def test_timer_parsing_and_pacing_budget():
    """Verifies countdown timer parsing and emergency threshold calculation."""
    assert parse_timer_text("15:00") == 900
    assert parse_timer_text("01:02:03") == 3723
    assert parse_timer_text("45 seconds") == 45

    normal_budget = compute_time_budget(remaining_seconds=600, questions_remaining=5)
    assert normal_budget["is_emergency"] is False
    assert normal_budget["per_question_budget_seconds"] == 20.0

    emergency_budget = compute_time_budget(remaining_seconds=30, questions_remaining=5)
    assert emergency_budget["is_emergency"] is True
    assert emergency_budget["per_question_budget_seconds"] <= 1.0


# =============================================================================
# 3. End-to-End Playwright Browser Automation Across All 5 Mock Portals (R2-R4)
# =============================================================================

@pytest.mark.parametrize("platform", ["hackerrank", "codility", "ccat", "testgorilla", "hirevue"])
def test_e2e_browser_solve_all_platforms(assessment_page, mock_portal_server, tmp_path, platform):
    """
    Full E2E test:
    Opens the simulated assessment portal in Playwright Chromium, solves all questions on the page
    (code, MCQs, math, logic sequences, STAR essays, SJT rankings, Likert sliders), submits,
    verifies 100% clean proctoring (0 blur violations), >=95% score, verification token,
    and JSONL audit log creation.
    """
    ledger_file = tmp_path / "assessments.jsonl"
    orchestrator = AssessmentOrchestrator()
    portal_url = f"{mock_portal_server}/portal/{platform}?session_id=e2e-{platform}-01"

    result = orchestrator.run_url(
        url=portal_url,
        company=f"TestCorp_{platform}",
        role="Software Engineer Intern",
        page=assessment_page,
        ledger_path=ledger_file,
    )

    assert result.status == "COMPLETED", f"Failed on {platform}: {result.error_message}"
    assert result.questions_total > 0
    assert result.questions_answered == result.questions_total
    assert result.proctoring_status == "CLEAN"
    assert result.score is not None and result.score >= 95.0
    assert result.passed is True
    assert result.verification_token is not None
    assert f"VERIFY-{platform.upper()}-" in result.verification_token

    # Verify JSONL audit log entry was written
    assert ledger_file.exists()
    lines = [json.loads(line) for line in ledger_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["company"] == f"TestCorp_{platform}"
    assert lines[0]["status"] == "COMPLETED"
    assert lines[0]["verification_token"] == result.verification_token


# =============================================================================
# 4. Notifications, Tracker Sync & CLI Entry Point Verification (R4)
# =============================================================================

def test_application_tracker_sync_updates_stage(tmp_path):
    """Verifies that completing an assessment updates applications.csv from OA/Assessment to Completed OA."""
    csv_file = tmp_path / "applications.csv"
    fieldnames = ["ID", "Company", "Role", "Stage", "Notes"]
    with open(csv_file, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({
            "ID": "932",
            "Company": "Optiver",
            "Role": "Quantitative Trading Intern",
            "Stage": "OA/Assessment",
            "Notes": "Pending HackerRank OA",
        })

    event = AssessmentRunResult(
        task_id="assess-optiver-01",
        status="COMPLETED",
        company="Optiver",
        role="Quantitative Trading Intern",
        platform=AssessmentPlatform.HACKERRANK,
        questions_total=8,
        questions_answered=8,
        score=100.0,
        passed=True,
    )

    ledger_file = tmp_path / "assessments.jsonl"
    outcomes = dispatch_completion(event, ledger_path=ledger_file, applications_csv=csv_file)
    assert outcomes["audit_log"] is True
    assert outcomes["tracker_sync"] is True

    with open(csv_file, mode="r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["Stage"] == "Completed OA / Awaiting Interview"
    assert "Assessment COMPLETED" in rows[0]["Notes"]


def test_orchestrator_cli_entry_points(capsys):
    """Verifies CLI --list-behavioral-questions and --dry-run execute cleanly with exit code 0."""
    rc1 = orchestrator_main(["--list-behavioral-questions"])
    assert rc1 == 0
    out1 = capsys.readouterr().out
    assert "ASSESSMENT_TEMPLATE_AMBIGUITY" in out1

    rc2 = orchestrator_main(["--source", "applications", "--dry-run", "--max-tasks", "3"])
    assert rc2 == 0
    out2 = capsys.readouterr().out
    assert "Processed" in out2


def test_extended_sympy_and_logic_capabilities():
    """Verifies newly hardened SymPy equation/modular math and cubic/Fibonacci sequence solvers."""
    solver = AssessmentSolver()

    # 1. SymPy linear equation
    q_eq = QuestionPayload(
        question_id="math-ext-01",
        category=QuestionCategory.MATHEMATICS,
        question_type=QuestionType.SHORT_TEXT,
        prompt_text="Solve for x: 3*x + 7 = 22",
    )
    ans_eq = solver.solve_domain_deterministic(q_eq)
    assert ans_eq.text_response == "5"

    # 2. Modular exponentiation
    q_mod = QuestionPayload(
        question_id="math-ext-02",
        category=QuestionCategory.MATHEMATICS,
        question_type=QuestionType.SHORT_TEXT,
        prompt_text="Calculate 7^256 mod 13",
    )
    ans_mod = solver.solve_domain_deterministic(q_mod)
    assert ans_mod.text_response == str(pow(7, 256, 13))

    # 3. Cubic sequence (n^3: 1, 8, 27, 64, 125, ? -> 216)
    q_cubic = QuestionPayload(
        question_id="logic-ext-01",
        category=QuestionCategory.LOGIC,
        question_type=QuestionType.SHORT_TEXT,
        prompt_text="Find the next number in the sequence: 1, 8, 27, 64, 125, ?",
    )
    ans_cubic = solver.solve_domain_deterministic(q_cubic)
    assert ans_cubic.text_response == "216"

    # 4. Fibonacci additive recurrence (2, 3, 5, 8, 13, 21, ? -> 34)
    q_fib = QuestionPayload(
        question_id="logic-ext-02",
        category=QuestionCategory.LOGIC,
        question_type=QuestionType.SHORT_TEXT,
        prompt_text="Find the next number in the sequence: 2, 3, 5, 8, 13, 21, ?",
    )
    ans_fib = solver.solve_domain_deterministic(q_fib)
    assert ans_fib.text_response == "34"


def test_extended_coding_algorithms_local_verification():
    """Verifies newly added OA coding algorithms compile and pass local test cases."""
    from assessments.contracts import CodeMetadata

    solver = AssessmentSolver()

    # Kadane's max_subarray
    q_kadane = QuestionPayload(
        question_id="tech-ext-01",
        category=QuestionCategory.TECHNICAL,
        question_type=QuestionType.CODE,
        prompt_text="Find the contiguous subarray with the largest sum.",
        code_metadata=CodeMetadata(
            entry_point="max_subarray",
            test_cases=[
                {"input": {"nums": [-2, 1, -3, 4, -1, 2, 1, -5, 4]}, "expected_output": 6},
                {"input": {"nums": [1]}, "expected_output": 1},
            ],
        ),
    )
    ans_k = solver.solve_domain_deterministic(q_kadane)
    assert "```" not in (ans_k.code_response or "")
    assert ans_k.confidence_score == 1.0

    # Binary search
    q_bs = QuestionPayload(
        question_id="tech-ext-02",
        category=QuestionCategory.TECHNICAL,
        question_type=QuestionType.CODE,
        prompt_text="Implement binary_search on a sorted array.",
        code_metadata=CodeMetadata(
            entry_point="binary_search",
            test_cases=[
                {"input": {"nums": [-1, 0, 3, 5, 9, 12], "target": 9}, "expected_output": 4},
                {"input": {"nums": [-1, 0, 3, 5, 9, 12], "target": 2}, "expected_output": -1},
            ],
        ),
    )
    ans_bs = solver.solve_domain_deterministic(q_bs)
    assert ans_bs.confidence_score == 1.0


def test_llm_json_fence_prose_stripping_and_env_alias(monkeypatch):
    """Verifies _strip_json_fences extracts JSON surrounded by prose and supports ASSESSMENT_PRIMARY_PROVIDER."""
    raw = 'Here is the JSON result:\n```json\n{"question_id": "q1", "text_response": "42"}\n```\nHope this helps!'
    cleaned = DualProviderLLMClient._strip_json_fences(raw)
    parsed = json.loads(cleaned)
    assert parsed["question_id"] == "q1"
    assert parsed["text_response"] == "42"

    raw_prose = 'Sure! The object is {"question_id": "q2", "confidence_score": 0.99} as requested.'
    cleaned2 = DualProviderLLMClient._strip_json_fences(raw_prose)
    assert json.loads(cleaned2)["question_id"] == "q2"

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ASSESSMENT_PRIMARY_PROVIDER", "openrouter")
    client = DualProviderLLMClient(fallback_enabled=True)
    assert client.get_provider_chain() == ["openrouter", "claude"]


def test_env_loaded_star_templates_score_100_on_grader():
    """Verifies that the full-length STAR templates in .env score 100.0% on the mock portal Grader."""
    from dotenv import dotenv_values

    grader = Grader()
    env_path = pathlib.Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        vals = dotenv_values(env_path)
        amb = vals.get("ASSESSMENT_TEMPLATE_AMBIGUITY", "")
        conf = vals.get("ASSESSMENT_TEMPLATE_CONFLICT", "")
        if not amb or not conf:
            pytest.skip("ASSESSMENT_TEMPLATE_* keys not configured in active .env")
        assert len(amb.split()) >= 120
        assert len(conf.split()) >= 120

        g1 = grader.grade_question("beh-star-001", {"text": amb})
        g2 = grader.grade_question("beh-star-002", {"text": conf})
        assert g1.score == 100.0 and g1.passed is True
        assert g2.score == 100.0 and g2.passed is True

