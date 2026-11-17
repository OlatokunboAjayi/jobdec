"""
Grading engine for Mock Assessment Portal.
Evaluates candidate submissions against ground-truth fixtures across all 4 cognitive domains:
behavioral, technical, mathematics, and logic.
"""

from __future__ import annotations

import json
import logging
import math
import pathlib
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import sympy as sp

from tests.mock_portal.models import (
    AnswerItem,
    CodeRunResponse,
    QuestionGradingDetail,
    QuestionModel,
    SubmitAssessmentRequest,
    SubmitAssessmentResponse,
)

logger = logging.getLogger("mock_portal.grader")
FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


class Grader:
    """
    Evaluates assessment answers against ground-truth datasets.
    """

    def __init__(self, fixtures_dir: Optional[pathlib.Path] = None):
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR
        self.fixtures: Dict[str, QuestionModel] = {}
        self.load_fixtures()

    def load_fixtures(self) -> None:
        """Loads all fixture JSON files into memory."""
        self.fixtures.clear()
        if not self.fixtures_dir.exists():
            logger.warning(f"Fixtures directory {self.fixtures_dir} does not exist.")
            return

        for path in self.fixtures_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for fix in data.get("fixtures", []):
                    model = QuestionModel.model_validate(fix)
                    self.fixtures[model.question_id] = model
            except Exception as e:
                logger.error(f"Failed to load fixture {path}: {e}")

    def get_question(self, question_id: str) -> Optional[QuestionModel]:
        return self.fixtures.get(question_id)

    def run_code_against_tests(
        self, question_id: str, code: str, language: str = "python"
    ) -> CodeRunResponse:
        """
        Executes submitted code against test cases in question code_metadata.
        """
        start_time = time.perf_counter()
        question = self.get_question(question_id)
        if not question or not question.code_metadata:
            return CodeRunResponse(
                status="ERROR",
                test_cases_passed=0,
                total_test_cases=0,
                output=f"Question {question_id} has no code metadata.",
                runtime_ms=0.0,
            )

        meta = question.code_metadata
        entry_point = meta.entry_point
        test_cases = meta.test_cases
        total_tests = len(test_cases)

        # Basic compile & exec
        ns: Dict[str, Any] = {}
        try:
            compiled = compile(code, "<submitted_code>", "exec")
            exec(compiled, ns)
        except Exception as e:
            runtime_ms = (time.perf_counter() - start_time) * 1000
            return CodeRunResponse(
                status="ERROR",
                test_cases_passed=0,
                total_test_cases=total_tests,
                output=f"Compilation/Syntax Error: {type(e).__name__}: {e}",
                runtime_ms=round(runtime_ms, 2),
            )

        if entry_point not in ns or not callable(ns[entry_point]):
            runtime_ms = (time.perf_counter() - start_time) * 1000
            return CodeRunResponse(
                status="ERROR",
                test_cases_passed=0,
                total_test_cases=total_tests,
                output=f"Function '{entry_point}' not found in submitted code.",
                runtime_ms=round(runtime_ms, 2),
            )

        fn = ns[entry_point]
        passed_count = 0
        output_lines: List[str] = []

        for idx, tc in enumerate(test_cases, 1):
            tc_id = tc.id
            tc_input = tc.input
            expected = tc.expected_output
            try:
                actual = fn(**tc_input)
                if actual == expected:
                    passed_count += 1
                    output_lines.append(f"Test Case {idx} ({tc_id}): PASSED")
                else:
                    output_lines.append(
                        f"Test Case {idx} ({tc_id}): FAILED. Input={tc_input}, Expected={expected}, Got={actual}"
                    )
            except Exception as ex:
                output_lines.append(f"Test Case {idx} ({tc_id}): EXCEPTION: {type(ex).__name__}: {ex}")

        runtime_ms = (time.perf_counter() - start_time) * 1000
        status = "PASSED" if passed_count == total_tests else "FAILED"
        if status == "PASSED":
            output_lines.append("All test cases passed successfully.")

        return CodeRunResponse(
            status=status,
            test_cases_passed=passed_count,
            total_test_cases=total_tests,
            output="\n".join(output_lines),
            runtime_ms=round(runtime_ms, 2),
        )

    def grade_question(self, question_id: str, raw_answer: Any) -> QuestionGradingDetail:
        """
        Grades an individual question answer against ground truth.
        """
        question = self.get_question(question_id)
        if not question:
            return QuestionGradingDetail(
                question_id=question_id,
                category="unknown",
                question_type="unknown",
                score=0.0,
                max_score=100.0,
                passed=False,
                feedback=f"Question '{question_id}' not found in fixtures.",
            )

        # Normalize raw answer into AnswerItem or dict
        answer: Dict[str, Any] = {}
        if isinstance(raw_answer, AnswerItem):
            answer = raw_answer.model_dump(exclude_none=True)
        elif isinstance(raw_answer, dict):
            answer = raw_answer
        elif isinstance(raw_answer, (str, int, float, list)):
            answer = {"value": raw_answer, "text": str(raw_answer)}

        qtype = question.question_type.lower()
        category = question.category.lower()

        if qtype == "single_choice":
            return self._grade_single_choice(question, answer)
        elif qtype == "multiple_choice":
            return self._grade_multiple_choice(question, answer)
        elif qtype == "slider":
            return self._grade_slider(question, answer)
        elif qtype == "ranking":
            return self._grade_ranking(question, answer)
        elif qtype == "short_text":
            return self._grade_short_text(question, answer)
        elif qtype == "essay":
            return self._grade_essay(question, answer)
        elif qtype == "code":
            return self._grade_code(question, answer)
        else:
            return QuestionGradingDetail(
                question_id=question_id,
                category=category,
                question_type=qtype,
                score=0.0,
                max_score=100.0,
                passed=False,
                feedback=f"Unsupported question type: {qtype}",
            )

    def _grade_single_choice(self, q: QuestionModel, answer: Dict[str, Any]) -> QuestionGradingDetail:
        expected = str(q.ground_truth_answer).strip().upper()
        # Answer might be in selected_option_id, selected_option_ids, text, or value
        user_val = (
            answer.get("selected_option_id")
            or (answer.get("selected_option_ids", [None])[0] if answer.get("selected_option_ids") else None)
            or answer.get("value")
            or answer.get("text")
            or ""
        )
        user_val = str(user_val).strip().upper()

        passed = user_val == expected
        score = 100.0 if passed else 0.0
        feedback = "Correct option selected." if passed else f"Expected {expected}, got {user_val}."
        return QuestionGradingDetail(
            question_id=q.question_id,
            category=q.category,
            question_type=q.question_type,
            score=score,
            max_score=100.0,
            passed=passed,
            feedback=feedback,
            details={"expected": expected, "received": user_val},
        )

    def _grade_multiple_choice(self, q: QuestionModel, answer: Dict[str, Any]) -> QuestionGradingDetail:
        expected_list = [str(x).strip().upper() for x in (q.ground_truth_answer or [])]
        expected_set = set(expected_list)
        user_list = answer.get("selected_option_ids") or answer.get("value") or []
        if isinstance(user_list, str):
            user_list = [user_list]
        user_set = {str(x).strip().upper() for x in user_list}

        if user_set == expected_set:
            score = 100.0
            passed = True
            feedback = "All correct options selected."
        else:
            # Partial credit: Jaccard or precision/recall
            intersection = user_set.intersection(expected_set)
            union = user_set.union(expected_set)
            score = round((len(intersection) / len(union)) * 100.0, 1) if union else 0.0
            passed = score >= 80.0
            feedback = f"Partial selection. Expected {sorted(expected_set)}, got {sorted(user_set)}."

        return QuestionGradingDetail(
            question_id=q.question_id,
            category=q.category,
            question_type=q.question_type,
            score=score,
            max_score=100.0,
            passed=passed,
            feedback=feedback,
            details={"expected": sorted(expected_set), "received": sorted(user_set)},
        )

    def _grade_slider(self, q: QuestionModel, answer: Dict[str, Any]) -> QuestionGradingDetail:
        expected = float(q.ground_truth_answer) if q.ground_truth_answer is not None else 3.0
        rubric = q.rubric or {}
        acceptable_range = rubric.get("acceptable_range", [expected, expected])

        val = answer.get("value")
        if val is None and answer.get("text"):
            try:
                val = float(answer["text"])
            except ValueError:
                pass

        if val is None:
            return QuestionGradingDetail(
                question_id=q.question_id,
                category=q.category,
                question_type=q.question_type,
                score=0.0,
                max_score=100.0,
                passed=False,
                feedback="No slider value provided.",
            )

        val_num = float(val)
        min_acc, max_acc = acceptable_range[0], acceptable_range[1]
        if min_acc <= val_num <= max_acc:
            score = 100.0
            passed = True
            feedback = f"Value {val_num} is within target range [{min_acc}, {max_acc}]."
        else:
            diff = min(abs(val_num - min_acc), abs(val_num - max_acc))
            score = max(0.0, 100.0 - diff * 30.0)
            passed = score >= 70.0
            feedback = f"Value {val_num} outside acceptable range [{min_acc}, {max_acc}]."

        return QuestionGradingDetail(
            question_id=q.question_id,
            category=q.category,
            question_type=q.question_type,
            score=score,
            max_score=100.0,
            passed=passed,
            feedback=feedback,
            details={"value": val_num, "target_range": acceptable_range},
        )

    def _grade_ranking(self, q: QuestionModel, answer: Dict[str, Any]) -> QuestionGradingDetail:
        expected: List[str] = [str(x).strip().upper() for x in (q.ground_truth_answer or [])]
        user_ranking = answer.get("ranking") or answer.get("value") or []

        # If user passed a dict mapping item -> rank (e.g. {"A": 2, "B": 4, "C": 1, "D": 3})
        if isinstance(user_ranking, dict):
            sorted_items = sorted(user_ranking.items(), key=lambda kv: int(kv[1]))
            user_order = [str(k).strip().upper() for k, _ in sorted_items]
        elif isinstance(user_ranking, list):
            user_order = [str(x).strip().upper() for x in user_ranking]
        else:
            user_order = []

        if len(user_order) != len(expected) or set(user_order) != set(expected):
            return QuestionGradingDetail(
                question_id=q.question_id,
                category=q.category,
                question_type=q.question_type,
                score=0.0,
                max_score=100.0,
                passed=False,
                feedback=f"Incomplete ranking. Expected permutations of {expected}, got {user_order}.",
            )

        # Count pair inversions
        n = len(expected)
        total_pairs = n * (n - 1) // 2
        inversions = 0
        exp_pos = {item: i for i, item in enumerate(expected)}
        for i in range(n):
            for j in range(i + 1, n):
                item_a, item_b = user_order[i], user_order[j]
                if exp_pos[item_a] > exp_pos[item_b]:
                    inversions += 1

        concordance_score = 100.0 * (total_pairs - inversions) / total_pairs if total_pairs > 0 else 100.0

        # Check negative penalties in rubric
        rubric = q.rubric or {}
        penalties = 0.0
        for neg in rubric.get("negative_penalties", []):
            cond = str(neg.get("condition", "")).lower()
            penalty = abs(float(neg.get("penalty", 0)))
            if "ranking option b as first or second" in cond:
                if len(user_order) >= 2 and ("B" in user_order[:2]):
                    penalties += penalty

        final_score = max(0.0, round(concordance_score - penalties, 1))
        passed = final_score >= 70.0
        feedback = f"Ranking score: {final_score}% (Inversions: {inversions}/{total_pairs}, Penalties: -{penalties})."

        return QuestionGradingDetail(
            question_id=q.question_id,
            category=q.category,
            question_type=q.question_type,
            score=final_score,
            max_score=100.0,
            passed=passed,
            feedback=feedback,
            details={"user_order": user_order, "expected_order": expected, "inversions": inversions},
        )

    def _grade_short_text(self, q: QuestionModel, answer: Dict[str, Any]) -> QuestionGradingDetail:
        expected = str(q.ground_truth_answer).strip()
        user_text = str(answer.get("text") or answer.get("value") or "").strip()

        if not user_text:
            return QuestionGradingDetail(
                question_id=q.question_id,
                category=q.category,
                question_type=q.question_type,
                score=0.0,
                max_score=100.0,
                passed=False,
                feedback="Empty answer submitted.",
            )

        rubric = q.rubric or {}
        acceptable = [expected.lower()]
        for eq in rubric.get("acceptable_equivalents", []):
            acceptable.append(str(eq).strip().lower())

        user_clean = user_text.lower()

        # 1. Direct or acceptable string match
        if user_clean in acceptable:
            return QuestionGradingDetail(
                question_id=q.question_id,
                category=q.category,
                question_type=q.question_type,
                score=100.0,
                max_score=100.0,
                passed=True,
                feedback=f"Exact or equivalent match: '{user_text}'.",
            )

        # 2. SymPy numeric / algebraic equivalence
        try:
            # Clean symbols / expressions
            t, s, e, x, y = sp.symbols("t s e x y")
            user_sym = sp.sympify(user_text)
            exp_sym = sp.sympify(expected)
            diff = sp.simplify(user_sym - exp_sym)
            if diff == 0 or abs(float(diff)) < rubric.get("tolerance", 1e-5):
                return QuestionGradingDetail(
                    question_id=q.question_id,
                    category=q.category,
                    question_type=q.question_type,
                    score=100.0,
                    max_score=100.0,
                    passed=True,
                    feedback=f"SymPy verified equivalent: {user_text} == {expected}.",
                )
        except Exception:
            pass

        # 3. Numeric float comparison
        try:
            u_f = float(user_text)
            e_f = float(expected)
            if math.isclose(u_f, e_f, rel_tol=1e-4, abs_tol=1e-4):
                return QuestionGradingDetail(
                    question_id=q.question_id,
                    category=q.category,
                    question_type=q.question_type,
                    score=100.0,
                    max_score=100.0,
                    passed=True,
                    feedback=f"Numerically equivalent: {u_f} == {e_f}.",
                )
        except ValueError:
            pass

        return QuestionGradingDetail(
            question_id=q.question_id,
            category=q.category,
            question_type=q.question_type,
            score=0.0,
            max_score=100.0,
            passed=False,
            feedback=f"Incorrect answer. Expected '{expected}', got '{user_text}'.",
            details={"expected": expected, "received": user_text},
        )

    def _grade_essay(self, q: QuestionModel, answer: Dict[str, Any]) -> QuestionGradingDetail:
        user_text = str(answer.get("text") or answer.get("value") or "").strip()
        rubric = q.rubric or {}
        required_keywords = [str(k).lower() for k in rubric.get("keywords_required", [])]

        if not user_text or len(user_text.split()) < 10:
            return QuestionGradingDetail(
                question_id=q.question_id,
                category=q.category,
                question_type=q.question_type,
                score=0.0,
                max_score=100.0,
                passed=False,
                feedback="Essay is empty or excessively brief (< 10 words).",
            )

        text_lower = user_text.lower()
        matched_keywords = [k for k in required_keywords if k in text_lower]
        keyword_ratio = len(matched_keywords) / len(required_keywords) if required_keywords else 1.0

        # Score calculation based on keyword density and STAR structure
        word_count = len(user_text.split())
        length_factor = min(1.0, word_count / 80.0)  # full credit at 80+ words

        base_score = 100.0 * (0.6 * keyword_ratio + 0.4 * length_factor)

        # Check negative penalties
        penalties = 0.0
        for neg in rubric.get("negative_penalties", []):
            cond = str(neg.get("condition", "")).lower()
            penalty = abs(float(neg.get("penalty", 0)))
            if "missing quantifiable metrics" in cond:
                # check if text contains numbers
                if not re.search(r"\b\d+\b", user_text):
                    penalties += penalty

        final_score = max(0.0, round(base_score - penalties, 1))
        passed = final_score >= 70.0
        feedback = (
            f"STAR evaluation score: {final_score}%. Matched {len(matched_keywords)}/{len(required_keywords)} "
            f"keywords. Words: {word_count}. Penalties: -{penalties}."
        )

        return QuestionGradingDetail(
            question_id=q.question_id,
            category=q.category,
            question_type=q.question_type,
            score=final_score,
            max_score=100.0,
            passed=passed,
            feedback=feedback,
            details={
                "word_count": word_count,
                "matched_keywords": matched_keywords,
                "missing_keywords": [k for k in required_keywords if k not in matched_keywords],
            },
        )

    def _grade_code(self, q: QuestionModel, answer: Dict[str, Any]) -> QuestionGradingDetail:
        code = str(answer.get("code") or answer.get("text") or "").strip()
        if not code:
            return QuestionGradingDetail(
                question_id=q.question_id,
                category=q.category,
                question_type=q.question_type,
                score=0.0,
                max_score=100.0,
                passed=False,
                feedback="No code submitted.",
            )

        run_res = self.run_code_against_tests(q.question_id, code)
        if run_res.total_test_cases == 0:
            score = 100.0 if run_res.status == "PASSED" else 0.0
        else:
            score = round((run_res.test_cases_passed / run_res.total_test_cases) * 100.0, 1)

        passed = score >= 75.0
        feedback = (
            f"Code test suite {run_res.status}: {run_res.test_cases_passed}/{run_res.total_test_cases} "
            f"passed in {run_res.runtime_ms}ms."
        )

        return QuestionGradingDetail(
            question_id=q.question_id,
            category=q.category,
            question_type=q.question_type,
            score=score,
            max_score=100.0,
            passed=passed,
            feedback=feedback,
            details={"run_output": run_res.output, "runtime_ms": run_res.runtime_ms},
        )

    def grade_submission(
        self, request: SubmitAssessmentRequest, target_question_ids: Optional[List[str]] = None
    ) -> SubmitAssessmentResponse:
        """
        Grades a full assessment submission and produces deterministic tokens.
        """
        question_ids = target_question_ids or list(request.answers.keys())
        if not question_ids:
            # Fall back to all questions for this platform
            question_ids = list(self.fixtures.keys())[:5]

        question_details: List[QuestionGradingDetail] = []
        total_score_sum = 0.0

        for qid in question_ids:
            ans = request.answers.get(qid, {})
            detail = self.grade_question(qid, ans)
            question_details.append(detail)
            total_score_sum += detail.score

        total_questions = len(question_ids)
        overall_score = round(total_score_sum / total_questions, 1) if total_questions > 0 else 0.0
        passed = overall_score >= 70.0
        violations = request.proctoring_violations or 0
        proctoring_status = "CLEAN" if violations == 0 else "FLAGGED"

        clean_platform = request.platform.upper().replace(" ", "_")
        session_id_clean = request.session_id.upper()
        verification_token = f"VERIFY-{clean_platform}-{overall_score}-{session_id_clean}"

        status = "COMPLETED" if overall_score >= 70.0 else ("PARTIAL" if overall_score > 0 else "FAILED")

        return SubmitAssessmentResponse(
            assessment_id=request.session_id,
            platform=request.platform,
            status=status,
            score=overall_score,
            passed=passed,
            proctoring_status=proctoring_status,
            verification_token=verification_token,
            redirect_url=f"/portal/results/{request.session_id}",
            details={
                "total_questions": total_questions,
                "correct_count": sum(1 for d in question_details if d.passed),
                "violations": violations,
                "question_details": [d.model_dump() for d in question_details],
            },
        )
