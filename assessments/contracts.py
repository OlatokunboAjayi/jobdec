"""
Typed Pydantic models and interface contracts for the Autonomous Assessment Solver (R2, R3, R4).
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator


class AssessmentPlatform(str, Enum):
    HACKERRANK = "hackerrank"
    CODILITY = "codility"
    CODESIGNAL = "codesignal"
    TESTGORILLA = "testgorilla"
    CRITERIA_CCAT = "criteria_ccat"
    CCAT = "ccat"
    HIREVUE = "hirevue"
    PYMETRICS = "pymetrics"
    SHL_TALENTCENTRAL = "shl_talentcentral"
    GOOGLE_FORMS = "google_forms"
    TYPEFORM = "typeform"
    MICROSOFT_FORMS = "microsoft_forms"
    WORKDAY = "workday_assessment"
    CUSTOM = "custom"


class QuestionCategory(str, Enum):
    BEHAVIORAL = "behavioral"
    TECHNICAL = "technical"
    MATHEMATICS = "mathematics"
    LOGIC = "logic"


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_TEXT = "short_text"
    ESSAY = "essay"
    CODE = "code"
    SLIDER = "slider"
    RANKING = "ranking"


class OptionItem(BaseModel):
    id: str
    label: str = ""
    text: str = ""
    description: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def sync_label_and_text(cls, values: Any) -> Any:
        if isinstance(values, dict):
            label = values.get("label") or values.get("text") or ""
            text = values.get("text") or values.get("label") or ""
            values["label"] = label
            values["text"] = text
        return values


class CodeMetadata(BaseModel):
    language: str = "python"
    entry_point: str = "solution"
    starter_code: str = ""
    starter_template: str = ""
    parameters: List[Dict[str, Any]] = Field(default_factory=list)
    return_type: Optional[str] = None
    test_cases: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def sync_starter_code(cls, values: Any) -> Any:
        if isinstance(values, dict):
            code = values.get("starter_code") or values.get("starter_template") or ""
            values["starter_code"] = code
            values["starter_template"] = code
        return values


class AssessmentTask(BaseModel):
    task_id: str
    company: str
    role: str = "Software Engineer"
    platform: AssessmentPlatform = AssessmentPlatform.CUSTOM
    raw_url: str = ""
    canonical_url: str
    deadline_iso: Optional[str] = None
    source_email_uid: Optional[str] = None
    candidate_email: str = ""
    target_role_category: str = "tech_swe_ai"
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def fill_raw_url(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if not values.get("raw_url") and values.get("canonical_url"):
                values["raw_url"] = values["canonical_url"]
        return values


class QuestionPayload(BaseModel):
    question_id: str
    sequence_number: int = 1
    total_questions: Optional[int] = None
    category: QuestionCategory = QuestionCategory.TECHNICAL
    question_type: QuestionType = QuestionType.SINGLE_CHOICE
    prompt_text: str = ""
    context: Optional[str] = None
    options: List[OptionItem] = Field(default_factory=list)
    code_metadata: Optional[CodeMetadata] = None
    input_specifications: Optional[Dict[str, Any]] = None
    rubric: Optional[Dict[str, Any]] = None
    screenshot_base64: Optional[str] = None
    remaining_time_seconds: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_prompt(cls, values: Any) -> Any:
        if isinstance(values, dict):
            if not values.get("prompt_text") and values.get("prompt"):
                values["prompt_text"] = values["prompt"]
        return values


class AnswerPayload(BaseModel):
    question_id: str
    selected_option_ids: List[str] = Field(default_factory=list)
    text_response: Optional[str] = None
    code_response: Optional[str] = None
    slider_value: Optional[Union[int, float]] = None
    ranking_order: Optional[List[str]] = None
    confidence_score: float = 1.0
    reasoning: str = ""
    solver_tier: str = "deterministic"

    def to_portal_submission_item(self, question_type: Union[QuestionType, str]) -> Dict[str, Any]:
        """Formats AnswerPayload into the mock portal / browser submission dictionary format."""
        qtype = str(question_type.value if isinstance(question_type, Enum) else question_type).lower()
        if qtype == "single_choice":
            sel = self.selected_option_ids[0] if self.selected_option_ids else (self.text_response or "A")
            return {"type": "single_choice", "selected_option_id": sel}
        elif qtype == "multiple_choice":
            return {"type": "multiple_choice", "selected_option_ids": list(self.selected_option_ids)}
        elif qtype == "slider":
            val = self.slider_value
            if val is None and self.text_response:
                try:
                    val = int(float(self.text_response))
                except ValueError:
                    val = 5
            elif val is None and self.selected_option_ids:
                try:
                    val = int(float(self.selected_option_ids[0]))
                except ValueError:
                    val = 5
            return {"type": "slider", "value": val if val is not None else 5}
        elif qtype == "ranking":
            order = self.ranking_order or list(self.selected_option_ids)
            return {"type": "ranking", "ranking": order}
        elif qtype == "code":
            return {"type": "code", "code": self.code_response or self.text_response or "", "language": "python"}
        elif qtype == "essay":
            return {"type": "essay", "text": self.text_response or ""}
        else:
            return {"type": "short_text", "text": self.text_response or ""}


class AssessmentRunResult(BaseModel):
    task_id: str
    status: Literal["COMPLETED", "PARTIAL", "FAILED", "TIMED_OUT"] = "COMPLETED"
    company: str = "Unknown"
    role: str = "Software Engineer"
    platform: Union[AssessmentPlatform, str] = AssessmentPlatform.CUSTOM
    assessment_url: str = ""
    questions_total: int = 0
    questions_answered: int = 0
    score: Optional[float] = None
    passed: Optional[bool] = None
    proctoring_status: str = "CLEAN"
    verification_token: Optional[str] = None
    start_time: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    end_time: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    duration_seconds: float = 0.0
    llm_provider: str = "stub"
    screenshots: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    answers_log: List[Dict[str, Any]] = Field(default_factory=list)
