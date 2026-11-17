"""
FastAPI Mock Assessment Portal Server.
Runs on 127.0.0.1:8888 and simulates assessment platforms including HackerRank,
Codility, TestGorilla, Criteria CCAT, and HireVue with ground-truth grading,
anti-proctoring event logging, and deterministic verification tokens.
"""

from __future__ import annotations

import datetime
import logging
import os
import pathlib
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tests.mock_portal.grader import Grader
from tests.mock_portal.models import (
    CodeRunRequest,
    CodeRunResponse,
    HealthResponse,
    ProctorEventRequest,
    ProctorEventResponse,
    QuestionModel,
    SessionState,
    SubmitAssessmentRequest,
    SubmitAssessmentResponse,
)

logger = logging.getLogger("mock_portal.server")

# Directories
PORTAL_DIR = pathlib.Path(__file__).resolve().parent
TEMPLATES_DIR = PORTAL_DIR / "templates"
STATIC_DIR = PORTAL_DIR / "static"

app = FastAPI(
    title="Mock Assessment Portal",
    description="Offline assessment simulation server for E2E testing",
    version="1.0.0",
)

# Static and Templates
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# In-memory storage & Grader instance
grader = Grader()
sessions: Dict[str, SessionState] = {}
results: Dict[str, SubmitAssessmentResponse] = {}
proctor_events: List[Dict[str, Any]] = []

PLATFORM_DEFAULT_TIMES = {
    "hackerrank": 3600,
    "codility": 2400,
    "testgorilla": 1800,
    "ccat": 900,
    "hirevue": 2700,
}

PLATFORM_TITLES = {
    "hackerrank": "HackerRank Technical Assessment",
    "codility": "Codility Algorithmic Assessment",
    "testgorilla": "TestGorilla Cognitive & Behavioral Assessment",
    "ccat": "Criteria CCAT Cognitive Aptitude Test",
    "hirevue": "HireVue Video & Behavioral Interview Assessment",
}


def get_platform_questions(platform: str, category_filter: Optional[str] = None) -> List[QuestionModel]:
    """Selects appropriate questions for a platform or category filter."""
    all_questions = list(grader.fixtures.values())
    clean_platform = platform.lower().strip()

    if category_filter:
        clean_cat = category_filter.lower().strip()
        filtered = [q for q in all_questions if q.category.lower() == clean_cat]
        return filtered if filtered else all_questions[:5]

    if clean_platform == "hackerrank":
        # Technical code and MCQs
        return [q for q in all_questions if q.category == "technical"]
    elif clean_platform == "codility":
        # Technical code problems primarily
        tech_code = [q for q in all_questions if q.category == "technical" and q.question_type == "code"]
        return tech_code if tech_code else [q for q in all_questions if q.category == "technical"]
    elif clean_platform == "ccat":
        # Logic and Math rapid battery
        return [q for q in all_questions if q.category in ("logic", "mathematics")]
    elif clean_platform == "testgorilla":
        # Mix of behavioral and technical
        return [q for q in all_questions if q.category in ("behavioral", "technical")]
    elif clean_platform == "hirevue":
        # Behavioral STAR essays and SJT rankings
        return [q for q in all_questions if q.category == "behavioral"]
    else:
        # Default sample across domains
        return all_questions[:6]


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Readiness and liveness probe for test harnesses."""
    port = int(os.getenv("MOCK_PORTAL_PORT", "8888"))
    return HealthResponse(
        status="ok",
        port=port,
        version="1.0.0",
        active_sessions=len(sessions),
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


@app.get("/hub", response_class=HTMLResponse)
def hub_dashboard(request: Request):
    """Hub directory and control center."""
    active_list = list(sessions.values())
    return templates.TemplateResponse(
        request=request,
        name="hub.html",
        context={
            "page_title": "Assessment Portal Hub",
            "active_sessions": active_list,
        },
    )


@app.get("/portal/{platform}", response_class=HTMLResponse)
def launch_portal(
    request: Request,
    platform: str,
    session_id: Optional[str] = Query(None),
    time_limit: Optional[int] = Query(None),
    strict_proctoring: bool = Query(True),
    category: Optional[str] = Query(None),
):
    """Renders simulated assessment interface for target platform."""
    clean_platform = platform.lower().strip()
    active_session_id = session_id or f"sess-{clean_platform}-{uuid.uuid4().hex[:8]}"

    questions = get_platform_questions(clean_platform, category_filter=category)
    duration = time_limit or PLATFORM_DEFAULT_TIMES.get(clean_platform, 1800)

    # Initialize or update session state
    if active_session_id not in sessions:
        sessions[active_session_id] = SessionState(
            session_id=active_session_id,
            platform=clean_platform,
            time_limit=duration,
            start_time=time.time(),
            strict_proctoring=strict_proctoring,
            category_filter=category,
            question_ids=[q.question_id for q in questions],
        )

    platform_title = PLATFORM_TITLES.get(clean_platform, f"{clean_platform.title()} Assessment")

    return templates.TemplateResponse(
        request=request,
        name="assessment.html",
        context={
            "page_title": f"{platform_title} - Mock Assessment",
            "platform": clean_platform,
            "platform_title": platform_title,
            "session_id": active_session_id,
            "time_limit": duration,
            "strict_proctoring": strict_proctoring,
            "questions": questions,
        },
    )


@app.post("/portal/api/run-code", response_model=CodeRunResponse)
def run_code_endpoint(payload: CodeRunRequest) -> CodeRunResponse:
    """Executes Python code in test cases for coding challenges."""
    return grader.run_code_against_tests(
        question_id=payload.question_id,
        code=payload.code,
        language=payload.language,
    )


@app.post("/portal/api/proctor-event", response_model=ProctorEventResponse)
def record_proctor_event(payload: ProctorEventRequest) -> ProctorEventResponse:
    """Records anti-proctoring tab switch / blur events."""
    event_record = {
        "session_id": payload.session_id,
        "event_type": payload.event_type,
        "timestamp": payload.timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    proctor_events.append(event_record)

    total_violations = 1
    session = sessions.get(payload.session_id)
    if session:
        session.violations += 1
        session.proctor_events.append(event_record)
        total_violations = session.violations

    return ProctorEventResponse(
        logged=True,
        session_id=payload.session_id,
        total_violations=total_violations,
        status="FLAGGED" if total_violations > 0 else "CLEAN",
    )


@app.post("/portal/api/submit", response_model=SubmitAssessmentResponse)
def submit_assessment_endpoint(payload: SubmitAssessmentRequest) -> SubmitAssessmentResponse:
    """Grades submitted answers, stores result, and generates verification tokens."""
    session = sessions.get(payload.session_id)
    target_qids = session.question_ids if session else None

    # Grade submission
    response = grader.grade_submission(payload, target_question_ids=target_qids)

    # Persist in session state
    if session:
        session.submitted = True
        session.submission_result = response
        session.answers = payload.answers

    results[payload.session_id] = response
    return response


@app.get("/portal/results/{session_id}", response_class=HTMLResponse)
def view_results(request: Request, session_id: str):
    """Renders final assessment outcome, score, breakdown, and verification token."""
    res = results.get(session_id)
    if not res:
        # Check if session exists and synthesize result
        session = sessions.get(session_id)
        if session and session.submission_result:
            res = session.submission_result
        else:
            raise HTTPException(status_code=404, detail=f"No results found for session '{session_id}'.")

    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "page_title": f"Results: {res.platform.title()} Assessment",
            "platform": res.platform,
            "session_id": session_id,
            "result": res,
        },
    )


@app.post("/portal/api/reset")
def reset_server_state() -> Dict[str, str]:
    """Resets all in-memory sessions, submissions, and proctoring logs."""
    sessions.clear()
    results.clear()
    proctor_events.clear()
    return {"status": "reset_successful"}


@app.get("/portal/api/session/{session_id}")
def get_session_info(session_id: str):
    """Programmatic session inspector for tests."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return session.model_dump()
