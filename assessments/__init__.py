"""
Autonomous assessment solver subsystem (Features R2, R3, R4).
Implements multi-platform link ingestion, browser automation,
multi-modal solving (behavioral, technical, mathematics, logic),
and completion notification dispatching.
"""

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
from assessments.extractor import (
    classify_platform,
    extract_assessment_tasks,
    extract_urls_from_text_or_html,
    ingest_from_applications_csv,
    ingest_from_inbox_cache,
    unwrap_redirect_url,
)
from assessments.browser import (
    ANTI_BLUR_INIT_SCRIPT,
    AssessmentBrowserDriver,
    compute_time_budget,
    parse_timer_text,
)
from assessments.llm_client import (
    BudgetExceededError,
    DualProviderLLMClient,
    LLMProviderError,
)
from assessments.templates import (
    BehavioralProfileConfig,
    get_behavioral_questions_catalog,
    load_behavioral_config,
)
from assessments.solver import (
    AssessmentSolver,
    get_solver,
    solve_question,
)
from assessments.notifier import (
    AssessmentNotifier,
    dispatch_completion,
    get_notifier,
)


def __getattr__(name: str):
    if name in ("AssessmentOrchestrator", "orchestrator_main"):
        from assessments.orchestrator import (
            AssessmentOrchestrator,
            main as orchestrator_main,
        )
        return AssessmentOrchestrator if name == "AssessmentOrchestrator" else orchestrator_main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AnswerPayload",
    "AssessmentPlatform",
    "AssessmentRunResult",
    "AssessmentTask",
    "CodeMetadata",
    "OptionItem",
    "QuestionCategory",
    "QuestionPayload",
    "QuestionType",
    "classify_platform",
    "extract_assessment_tasks",
    "extract_urls_from_text_or_html",
    "ingest_from_applications_csv",
    "ingest_from_inbox_cache",
    "unwrap_redirect_url",
    "ANTI_BLUR_INIT_SCRIPT",
    "AssessmentBrowserDriver",
    "compute_time_budget",
    "parse_timer_text",
    "BudgetExceededError",
    "DualProviderLLMClient",
    "LLMProviderError",
    "BehavioralProfileConfig",
    "get_behavioral_questions_catalog",
    "load_behavioral_config",
    "AssessmentSolver",
    "get_solver",
    "solve_question",
    "AssessmentNotifier",
    "dispatch_completion",
    "get_notifier",
    "AssessmentOrchestrator",
    "orchestrator_main",
]
