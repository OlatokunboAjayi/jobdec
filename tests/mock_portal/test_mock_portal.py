"""
Comprehensive Unit and Integration Test Suite for Mock Assessment Portal.
Verifies server endpoints, fixture validity, code runner, grading engine,
anti-proctoring tab retention, StubLLMProvider deterministic answering,
and failure simulation failover modes.
"""

from __future__ import annotations

import json
import pathlib
import pytest
from pydantic import BaseModel

from tests.mock_portal.grader import Grader
from tests.mock_portal.models import (
    AnswerItem,
    CodeRunRequest,
    ProctorEventRequest,
    SubmitAssessmentRequest,
)
from tests.mock_portal.stub_provider import (
    SimulationMode,
    StubAuthError,
    StubLLMProvider,
    StubRateLimitError,
    StubServerError,
    StubTimeoutError,
)

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


# =============================================================================
# 1. Server Health and Portal Route Tests
# =============================================================================

def test_server_health_endpoint(portal_client):
    """Verifies /health probe returns 200 OK and expected JSON schema."""
    resp = portal_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["port"] == 8888
    assert "timestamp" in data
    assert isinstance(data["active_sessions"], int)


def test_hub_dashboard_rendering(portal_client):
    """Verifies /hub renders the HTML hub directory with all 5 platform launch links."""
    resp = portal_client.get("/hub")
    assert resp.status_code == 200
    text = resp.text
    assert "HackerRank" in text
    assert "Codility" in text
    assert "TestGorilla" in text
    assert "Criteria CCAT" in text
    assert "HireVue" in text
    assert 'data-testid="hub-container"' in text
    assert 'data-testid="btn-reset-server"' in text


@pytest.mark.parametrize("platform", ["hackerrank", "codility", "ccat", "testgorilla", "hirevue"])
def test_portal_platform_routes(portal_client, platform):
    """Verifies /portal/{platform} renders assessment pages with platform identity and testids."""
    resp = portal_client.get(f"/portal/{platform}")
    assert resp.status_code == 200
    html = resp.text
    assert 'data-testid="portal-header"' in html
    assert 'data-testid="platform-title"' in html
    assert f'data-platform="{platform}"' in html
    assert 'data-testid="timer"' in html
    assert 'data-testid="question-card"' in html
    assert 'data-testid="btn-submit-assessment"' in html


def test_portal_query_parameter_overrides(portal_client):
    """Verifies custom time_limit, category filter, and session_id query parameters."""
    custom_session = "custom-test-sess-99"
    resp = portal_client.get(
        f"/portal/hackerrank?session_id={custom_session}&time_limit=1200&category=technical&strict_proctoring=false"
    )
    assert resp.status_code == 200
    html = resp.text
    assert custom_session in html
    assert 'data-seconds-remaining="1200"' in html
    assert 'data-strict-proctoring="false"' in html


# =============================================================================
# 2. Fixture Loading and Schema Validity Tests
# =============================================================================

def test_fixtures_file_presence_and_count():
    """Verifies all 4 domain fixture JSON files exist and contain at least 7 items each."""
    expected_files = [
        "behavioral_fixtures.json",
        "technical_fixtures.json",
        "math_fixtures.json",
        "logic_fixtures.json",
    ]
    for fname in expected_files:
        fpath = FIXTURES_DIR / fname
        assert fpath.exists(), f"Missing fixture file: {fname}"
        data = json.loads(fpath.read_text(encoding="utf-8"))
        assert "fixtures" in data
        assert len(data["fixtures"]) >= 7, f"File {fname} has fewer than 7 fixtures"


def test_fixtures_schema_and_ground_truth_payloads():
    """Verifies every fixture has question_id, category, ground_truth_answer, and valid payload."""
    grader = Grader()
    assert len(grader.fixtures) == 31, f"Expected 31 fixtures, got {len(grader.fixtures)}"

    for qid, q in grader.fixtures.items():
        assert q.question_id == qid
        assert q.category in ("behavioral", "technical", "mathematics", "logic")
        assert q.question_type in (
            "single_choice",
            "multiple_choice",
            "short_text",
            "essay",
            "slider",
            "ranking",
            "code",
        )
        assert q.prompt and len(q.prompt.strip()) > 5
        assert q.ground_truth_answer is not None
        assert q.ground_truth_payload is not None
        # Check payload contract conformance
        gt = q.ground_truth_payload
        assert "selected_option_ids" in gt
        assert "confidence_score" in gt
        assert gt["confidence_score"] >= 0.90


# =============================================================================
# 3. Code Runner Endpoint Tests (/portal/api/run-code)
# =============================================================================

def test_run_code_success(portal_client):
    """Verifies valid Python code solution runs and passes all 6 test cases for Two Sum."""
    valid_code = (
        "def two_sum(nums: list[int], target: int) -> list[int]:\n"
        "    seen = {}\n"
        "    for i, num in enumerate(nums):\n"
        "        comp = target - num\n"
        "        if comp in seen:\n"
        "            return [seen[comp], i]\n"
        "        seen[num] = i\n"
        "    return []\n"
    )
    req = CodeRunRequest(
        session_id="run-test-sess",
        question_id="tech-code-two-sum",
        language="python",
        code=valid_code,
    )
    resp = portal_client.post("/portal/api/run-code", json=req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "PASSED"
    assert data["test_cases_passed"] == 6
    assert data["total_test_cases"] == 6
    assert "All test cases passed successfully" in data["output"]
    assert data["runtime_ms"] >= 0.0


def test_run_code_syntax_error(portal_client):
    """Verifies invalid Python syntax returns status ERROR with clear diagnostic message."""
    bad_code = "def two_sum(nums, target) This is syntax error!!!"
    req = CodeRunRequest(
        session_id="run-test-sess",
        question_id="tech-code-two-sum",
        language="python",
        code=bad_code,
    )
    resp = portal_client.post("/portal/api/run-code", json=req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ERROR"
    assert data["test_cases_passed"] == 0
    assert "SyntaxError" in data["output"]


def test_run_code_failed_logic(portal_client):
    """Verifies incorrect algorithm returns status FAILED with failed test case details."""
    wrong_code = "def two_sum(nums: list[int], target: int) -> list[int]:\n    return [0, 0]\n"
    req = CodeRunRequest(
        session_id="run-test-sess",
        question_id="tech-code-two-sum",
        language="python",
        code=wrong_code,
    )
    resp = portal_client.post("/portal/api/run-code", json=req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FAILED"
    assert data["test_cases_passed"] < data["total_test_cases"]


# =============================================================================
# 4. Proctor Event Logging Tests (/portal/api/proctor-event)
# =============================================================================

def test_proctor_event_logging(portal_client):
    """Verifies proctor events increment session violation counter and status becomes FLAGGED."""
    sess_id = "proctor-audit-session"
    # First visit the portal to create session
    portal_client.get(f"/portal/hackerrank?session_id={sess_id}")

    # Fire blur event
    req1 = ProctorEventRequest(session_id=sess_id, event_type="blur")
    resp1 = portal_client.post("/portal/api/proctor-event", json=req1.model_dump())
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["logged"] is True
    assert data1["total_violations"] == 1
    assert data1["status"] == "FLAGGED"

    # Fire visibilitychange event
    req2 = ProctorEventRequest(session_id=sess_id, event_type="visibilitychange")
    resp2 = portal_client.post("/portal/api/proctor-event", json=req2.model_dump())
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total_violations"] == 2
    assert data2["status"] == "FLAGGED"

    # Inspect session API
    sess_resp = portal_client.get(f"/portal/api/session/{sess_id}")
    assert sess_resp.status_code == 200
    assert sess_resp.json()["violations"] == 2


# =============================================================================
# 5. Submission Grading and Results Token Tests (/portal/api/submit)
# =============================================================================

def test_submit_assessment_clean_pass(portal_client):
    """Verifies submission with correct answers grades to 100%, generates token, and status is COMPLETED."""
    sess_id = "submit-pass-session"
    # Launch CCAT with 4 specific questions
    session_resp = portal_client.get(f"/portal/ccat?session_id={sess_id}")
    assert session_resp.status_code == 200

    # Get the questions in this session
    sess_data = portal_client.get(f"/portal/api/session/{sess_id}").json()
    qids = sess_data["question_ids"]

    # Provide correct answers for all questions in this session
    grader = Grader()
    answers = {}
    for qid in qids:
        q = grader.get_question(qid)
        if q:
            if q.question_type == "single_choice":
                answers[qid] = {"type": "single_choice", "selected_option_id": q.ground_truth_answer}
            elif q.question_type == "short_text":
                answers[qid] = {"type": "short_text", "text": str(q.ground_truth_answer)}
            elif q.question_type == "code":
                answers[qid] = {"type": "code", "code": q.ground_truth_answer}
            elif q.question_type == "slider":
                answers[qid] = {"type": "slider", "value": q.ground_truth_answer}
            elif q.question_type == "ranking":
                answers[qid] = {"type": "ranking", "ranking": q.ground_truth_answer}

    sub_req = SubmitAssessmentRequest(
        session_id=sess_id,
        platform="ccat",
        candidate_id="cand_test_user",
        elapsed_time_seconds=240.0,
        proctoring_violations=0,
        answers=answers,
    )

    resp = portal_client.post("/portal/api/submit", json=sub_req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["assessment_id"] == sess_id
    assert data["platform"] == "ccat"
    assert data["score"] >= 95.0
    assert data["passed"] is True
    assert data["status"] == "COMPLETED"
    assert data["proctoring_status"] == "CLEAN"
    assert "VERIFY-CCAT-" in data["verification_token"]
    assert data["redirect_url"] == f"/portal/results/{sess_id}"

    # Results page HTML rendering check
    res_page = portal_client.get(f"/portal/results/{sess_id}")
    assert res_page.status_code == 200
    assert data["verification_token"] in res_page.text
    assert 'data-testid="verification-token"' in res_page.text


def test_submit_assessment_with_violations_flagged(portal_client):
    """Verifies submission with proctoring violations is labeled FLAGGED."""
    sess_id = "submit-flagged-session"
    answers = {
        "logic-seq-num-poly-01": {"text": "48"},
    }
    sub_req = SubmitAssessmentRequest(
        session_id=sess_id,
        platform="hackerrank",
        proctoring_violations=3,
        answers=answers,
    )
    resp = portal_client.post("/portal/api/submit", json=sub_req.model_dump())
    assert resp.status_code == 200
    data = resp.json()
    assert data["proctoring_status"] == "FLAGGED"
    assert data["details"]["violations"] == 3


def test_server_reset_endpoint(portal_client):
    """Verifies /portal/api/reset clears all session states and proctor events."""
    portal_client.get("/portal/hackerrank?session_id=reset-me")
    reset_resp = portal_client.post("/portal/api/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["status"] == "reset_successful"

    health = portal_client.get("/health").json()
    assert health["active_sessions"] == 0


# =============================================================================
# 6. Grader Multi-Domain Evaluation Tests
# =============================================================================

def test_grader_behavioral_essay_keywords():
    """Verifies behavioral STAR essay grading with candidate profile keywords."""
    grader = Grader()
    qid = "beh-star-001"
    # Full candidate narrative containing Delta, 50,000, 150, Python, validation, automation with > 80 words
    good_text = (
        "Situation: While working on frontline shift bidding operations for thousands of employees at Delta Air Lines, "
        "station bidding rules were loosely defined across hub stations with legacy manual processes. "
        "Task: My core engineering task was to design and deploy an automated shift-selection pipeline that eliminated "
        "manual spreadsheet coordination while guaranteeing zero bid corruption. "
        "Action: I translated complex requirements into an automated Python automation pipeline. I implemented data validation "
        "rules that screened 3,000+ workflow records and flagged over 2,500 exceptions for proactive resolution. "
        "Result: The Python automation pipeline eliminated over 150 hours of manual coordination and processed over 50,000 employee bids with 100% integrity."
    )
    res = grader.grade_question(qid, {"type": "essay", "text": good_text})
    assert res.score >= 80.0
    assert res.passed is True

    # Empty text fails
    res_empty = grader.grade_question(qid, {"type": "essay", "text": ""})
    assert res_empty.score == 0.0
    assert res_empty.passed is False


def test_grader_behavioral_sjt_ranking():
    """Verifies SJT ranking Kendall Tau correlation and penalty evaluation."""
    grader = Grader()
    qid = "beh-sjt-001"
    # Ground truth is ['C', 'A', 'D', 'B']
    res_perfect = grader.grade_question(qid, {"ranking": ["C", "A", "D", "B"]})
    assert res_perfect.score == 100.0
    assert res_perfect.passed is True

    # Ranking B as first triggers penalty condition in rubric
    res_penalized = grader.grade_question(qid, {"ranking": ["B", "C", "A", "D"]})
    assert res_penalized.score < 50.0


def test_grader_math_sympy_equivalence():
    """Verifies mathematical SymPy exact calculation and equivalent forms."""
    grader = Grader()
    qid = "math-work-pipes-01"
    # Ground truth is 24/5
    for valid_answer in ["24/5", "4.8", "4.80"]:
        res = grader.grade_question(qid, {"text": valid_answer})
        assert res.score == 100.0, f"Failed on valid form {valid_answer}"

    res_wrong = grader.grade_question(qid, {"text": "10"})
    assert res_wrong.score == 0.0


# =============================================================================
# 7. StubLLMProvider Deterministic Answering & Failure Simulation Tests
# =============================================================================

def test_stub_provider_ground_truth_answering(stub_provider):
    """Verifies StubLLMProvider answers questions with ground truth in 0 tokens."""
    res = stub_provider.solve_question({"question_id": "tech-code-two-sum"})
    assert res["question_id"] == "tech-code-two-sum"
    assert res["confidence_score"] == 1.0
    assert "def two_sum" in res["code_response"]
    assert res["solver_tier"] in ("ground_truth", "deterministic_stub")


def test_stub_provider_prompt_hash_lookup(stub_provider):
    """Verifies StubLLMProvider resolves questions by prompt text hash without explicit question_id."""
    fixture = stub_provider.fixtures_by_id["math-work-pipes-01"]
    prompt = fixture["prompt"]
    res = stub_provider.solve_question({"prompt_text": prompt})
    assert res["confidence_score"] == 1.0
    assert res["text_response"] == "24/5"


def test_stub_provider_unmapped_fallback(stub_provider):
    """Verifies StubLLMProvider gracefully provides deterministic fallback for unmapped questions."""
    res = stub_provider.solve_question({
        "question_id": "unmapped_question_999",
        "prompt_text": "What is 20 plus 22?",
        "category": "mathematics",
        "question_type": "short_text",
    })
    assert res["confidence_score"] == 0.5
    assert res["solver_tier"] == "stub_fallback"
    assert res["text_response"] is not None


def test_stub_provider_simulate_rate_limit(stub_provider):
    """Verifies simulate_rate_limit context manager raises 429 then recovers."""
    with stub_provider.simulate_rate_limit(provider="claude", count=1):
        # First call fails
        with pytest.raises(StubRateLimitError):
            stub_provider.solve_question({"question_id": "tech-code-two-sum"}, provider_name="claude")
        # Subsequent call succeeds after failure budget is exhausted
        res = stub_provider.solve_question({"question_id": "tech-code-two-sum"}, provider_name="claude")
        assert res["confidence_score"] == 1.0


def test_stub_provider_simulate_failover_provider_targeting(stub_provider):
    """Verifies failure simulation targets one provider while letting fallback succeed."""
    with stub_provider.simulate_rate_limit(provider="claude", count=5):
        # Claude fails
        with pytest.raises(StubRateLimitError):
            stub_provider.solve_question({"question_id": "tech-code-two-sum"}, provider_name="claude")
        # OpenRouter succeeds immediately
        res = stub_provider.solve_question({"question_id": "tech-code-two-sum"}, provider_name="openrouter")
        assert res["confidence_score"] == 1.0


def test_stub_provider_simulate_timeout(stub_provider):
    """Verifies simulate_timeout triggers StubTimeoutError."""
    with stub_provider.simulate_timeout(count=1):
        with pytest.raises(StubTimeoutError):
            stub_provider.solve_question({"question_id": "math-work-pipes-01"})


def test_stub_provider_simulate_auth_error(stub_provider):
    """Verifies simulate_auth_error triggers StubAuthError."""
    with stub_provider.simulate_auth_error():
        with pytest.raises(StubAuthError):
            stub_provider.solve_question({"question_id": "math-work-pipes-01"})


class MockStructuredSchema(BaseModel):
    selected_option_ids: list[str] = []
    text_response: str = ""
    confidence_score: float = 1.0


def test_stub_provider_ask_structured_output(stub_provider):
    """Verifies ask() interface returns validated Pydantic model for llm_client compatibility."""
    res = stub_provider.ask(
        agent="assessment_agent",
        tier="fast",
        system="Answer question",
        user="Two Sum prompt",
        schema=MockStructuredSchema,
    )
    assert isinstance(res, MockStructuredSchema)
    assert hasattr(res, "confidence_score")


# =============================================================================
# 8. Browser Automation and Anti-Proctoring Integration Test (Playwright)
# =============================================================================

def test_browser_page_dom_and_anti_blur_suppression(assessment_page, mock_portal_server):
    """
    Playwright integration test:
    - Navigates to live mock server at http://127.0.0.1:8888/portal/hackerrank
    - Inspects rendered DOM elements (title, timer, editor, actions)
    - Emulates window blur event and confirms anti-blur script prevents violation increments
    """
    assessment_page.goto(f"{mock_portal_server}/portal/hackerrank")

    # 1. Assert DOM elements
    title_el = assessment_page.locator('[data-testid="platform-title"]')
    assert title_el.inner_text() == "HackerRank Technical Assessment"

    timer_el = assessment_page.locator('[data-testid="timer"]')
    assert int(timer_el.get_attribute("data-seconds-remaining")) > 0

    # 2. Check code editor
    code_el = assessment_page.locator('[data-testid="code-editor"]').first
    assert code_el.is_visible()

    # 3. Simulate blur event to verify ANTI_BLUR_INIT_SCRIPT suppression
    # The pre-injected script suppresses blur in the capture phase, so proctor violations remain 0
    assessment_page.evaluate("window.dispatchEvent(new Event('blur'))")
    violations_badge = assessment_page.locator('[data-testid="proctor-violations"]')
    count_attr = violations_badge.get_attribute("data-violation-count") or "0"
    assert count_attr == "0", f"Proctor violations were triggered: {count_attr}"
