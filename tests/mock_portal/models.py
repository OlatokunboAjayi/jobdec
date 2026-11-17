"""
Pydantic data models for Mock Assessment Portal.
Defines typed schemas for requests, responses, fixture entities, sessions, and grading.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class OptionItem(BaseModel):
    id: str
    text: str


class CodeTestCase(BaseModel):
    id: str
    is_public: bool = True
    input: Dict[str, Any] = Field(default_factory=dict)
    expected_output: Any


class CodeMetadata(BaseModel):
    entry_point: str
    language: str = "python"
    starter_template: str = ""
    test_cases: List[CodeTestCase] = Field(default_factory=list)


class QuestionModel(BaseModel):
    question_id: str
    category: str
    question_type: str
    difficulty: Optional[str] = "medium"
    estimated_time_seconds: Optional[int] = 120
    prompt: str
    context: Optional[str] = None
    options: Optional[List[OptionItem]] = None
    code_metadata: Optional[CodeMetadata] = None
    input_specifications: Optional[Dict[str, Any]] = None
    ground_truth_answer: Optional[Any] = None
    sympy_verification: Optional[Dict[str, Any]] = None
    ground_truth_payload: Optional[Dict[str, Any]] = None
    explanation: Optional[str] = None
    rubric: Optional[Dict[str, Any]] = None


class CodeRunRequest(BaseModel):
    session_id: str
    question_id: str
    language: str = "python"
    code: str


class CodeRunResponse(BaseModel):
    status: str  # "PASSED", "FAILED", "ERROR"
    test_cases_passed: int
    total_test_cases: int
    output: str
    runtime_ms: float


class ProctorEventRequest(BaseModel):
    session_id: str
    event_type: str  # "blur", "visibilitychange", etc.
    timestamp: Optional[str] = None


class ProctorEventResponse(BaseModel):
    logged: bool = True
    session_id: str
    total_violations: int
    status: str  # "CLEAN", "FLAGGED"


class AnswerItem(BaseModel):
    type: Optional[str] = None
    selected_option_id: Optional[str] = None
    selected_option_ids: Optional[List[str]] = None
    text: Optional[str] = None
    code: Optional[str] = None
    language: Optional[str] = "python"
    value: Optional[Union[int, float]] = None
    ranking: Optional[List[str]] = None


class SubmitAssessmentRequest(BaseModel):
    session_id: str
    platform: str
    candidate_id: Optional[str] = "cand_001"
    elapsed_time_seconds: Optional[float] = 0.0
    proctoring_violations: Optional[int] = 0
    answers: Dict[str, Union[AnswerItem, Dict[str, Any], Any]] = Field(default_factory=dict)


class QuestionGradingDetail(BaseModel):
    question_id: str
    category: str
    question_type: str
    score: float
    max_score: float = 100.0
    passed: bool
    feedback: str
    details: Optional[Dict[str, Any]] = None


class SubmitAssessmentResponse(BaseModel):
    assessment_id: str
    platform: str
    status: str  # "COMPLETED", "PARTIAL", "FAILED"
    score: float
    passed: bool
    proctoring_status: str  # "CLEAN", "FLAGGED"
    verification_token: str
    redirect_url: str
    details: Dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str = "ok"
    port: int = 8888
    version: str = "1.0.0"
    active_sessions: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class SessionState(BaseModel):
    session_id: str
    platform: str
    time_limit: int
    start_time: float
    candidate_id: str = "cand_001"
    strict_proctoring: bool = True
    category_filter: Optional[str] = None
    question_ids: List[str] = Field(default_factory=list)
    violations: int = 0
    proctor_events: List[Dict[str, Any]] = Field(default_factory=list)
    answers: Dict[str, Any] = Field(default_factory=dict)
    submitted: bool = False
    submission_result: Optional[SubmitAssessmentResponse] = None
