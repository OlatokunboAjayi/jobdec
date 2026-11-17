"""
Multi-Modal Assessment Solver Facade (Features 13-18, R3).
Implements `solve_question(context: QuestionPayload) -> AnswerPayload` and `AssessmentSolver`,
coordinating the DualProviderLLMClient (Claude + OpenRouter) and the 4 specialized cognitive
domain solvers: Behavioral, Technical, Mathematics, and Logic.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

from assessments.contracts import (
    AnswerPayload,
    QuestionCategory,
    QuestionPayload,
    QuestionType,
)
from assessments.domains.behavioral import BehavioralSolver
from assessments.domains.logic import LogicSolver
from assessments.domains.mathematics import MathematicsSolver
from assessments.domains.technical import TechnicalSolver
from assessments.llm_client import DualProviderLLMClient


class AssessmentSolver:
    """
    Multi-modal solver routing questions across Behavioral, Technical, Mathematics,
    and Logic engines with Dual-Provider LLM support and zero-hallucination fallbacks.
    """

    def __init__(
        self,
        llm_client: Optional[DualProviderLLMClient] = None,
        prefer_llm: Optional[bool] = None,
    ) -> None:
        if os.getenv("ASSESSMENT_TEST_MODE", "").lower() not in ("true", "1", "yes"):
            try:
                from dotenv import load_dotenv
                import candidate as CAND
                if CAND.ENV.exists():
                    load_dotenv(CAND.ENV, override=False)
            except Exception:
                pass
        self.llm_client = llm_client or DualProviderLLMClient()
        if prefer_llm is None:
            has_live_key = any(
                self.llm_client._is_valid_api_key(os.getenv(k, ""))
                for k in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
            )
            self.prefer_llm = has_live_key
        else:
            self.prefer_llm = prefer_llm
        self._session_history: List[Dict[str, str]] = []
        self.behavioral_solver = BehavioralSolver(llm_client=self.llm_client)
        self.technical_solver = TechnicalSolver(llm_client=self.llm_client)
        self.mathematics_solver = MathematicsSolver(llm_client=self.llm_client)
        self.logic_solver = LogicSolver(llm_client=self.llm_client)

    @staticmethod
    def coerce_payload(context: Union[QuestionPayload, Dict[str, Any], Any]) -> QuestionPayload:
        if isinstance(context, QuestionPayload):
            return context
        if isinstance(context, dict):
            data = dict(context)
            if "question_id" not in data or not data["question_id"]:
                data["question_id"] = "q_auto"
            if "prompt_text" not in data and "prompt" in data:
                data["prompt_text"] = data["prompt"]
            if "category" not in data or not data["category"]:
                data["category"] = AssessmentSolver.infer_category(
                    data.get("prompt_text", ""),
                    data.get("question_type", "single_choice"),
                ).value
            return QuestionPayload.model_validate(data)
        # Object with attributes
        qid = getattr(context, "question_id", "q_auto")
        prompt = getattr(context, "prompt_text", None) or getattr(context, "prompt", "")
        cat = getattr(context, "category", None) or AssessmentSolver.infer_category(prompt, "single_choice")
        qtype = getattr(context, "question_type", QuestionType.SINGLE_CHOICE)
        options = getattr(context, "options", []) or []
        code_meta = getattr(context, "code_metadata", None)
        return QuestionPayload(
            question_id=qid,
            category=cat,
            question_type=qtype,
            prompt_text=prompt,
            options=options,
            code_metadata=code_meta,
        )

    @staticmethod
    def infer_category(prompt_text: str, question_type: str = "") -> QuestionCategory:
        prompt_l = (prompt_text or "").lower()
        qtype_l = (question_type or "").lower()

        if qtype_l in ("code",):
            return QuestionCategory.TECHNICAL
        if qtype_l in ("slider", "ranking", "essay"):
            return QuestionCategory.BEHAVIORAL

        if any(k in prompt_l for k in ("situation", "teammate", "disagreement", "level of agreement", "most effective", "star")):
            return QuestionCategory.BEHAVIORAL
        if any(k in prompt_l for k in ("python", "sql", "hash table", "time complexity", "gil", "dense_rank", "function", "array")):
            return QuestionCategory.TECHNICAL
        if any(k in prompt_l for k in ("sequence", "premises", "syllogism", "contrapositive", "3x3 pattern", "assumption", "deployed before")):
            return QuestionCategory.LOGIC
        if any(k in prompt_l for k in ("reservoir", "probability", "prevalence", "mph", "dice", "saline", "isotope", "profit", "calculate")):
            return QuestionCategory.MATHEMATICS

        return QuestionCategory.TECHNICAL

    def solve_domain_deterministic(self, payload: QuestionPayload) -> AnswerPayload:
        """Executes the specialized domain solver for a QuestionPayload."""
        cat = str(payload.category.value if hasattr(payload.category, "value") else payload.category).lower()
        if cat == "behavioral":
            return self.behavioral_solver.solve(payload)
        elif cat == "technical":
            return self.technical_solver.solve(payload)
        elif cat == "mathematics":
            return self.mathematics_solver.solve(payload)
        elif cat == "logic":
            return self.logic_solver.solve(payload)
        else:
            inferred = self.infer_category(payload.prompt_text, str(payload.question_type))
            payload.category = inferred
            return self.solve_domain_deterministic(payload)

    def solve_question(self, context: Union[QuestionPayload, Dict[str, Any], Any]) -> AnswerPayload:
        """
        Primary entry point matching PROJECT.md R3 contract:
        `solver.solve_question(context: QuestionPayload) -> AnswerPayload`
        """
        payload = self.coerce_payload(context)

        # First run the domain-specialized engine (SymPy, AST code verifier, SJT/STAR/Likert, sequence reasoner)
        domain_answer = self.solve_domain_deterministic(payload)
        final_ans = domain_answer

        # If prefer_llm is active or stub provider has an exact ground-truth fixture for this question_id,
        # consult the DualProviderLLMClient with domain_answer + session history as the zero-loss baseline.
        if self.prefer_llm or self.llm_client._get_stub() is not None:
            try:
                llm_context: Any = payload
                if self.llm_client._get_stub() is None:
                    llm_context = {
                        "question": payload.model_dump(),
                        "deterministic_baseline": domain_answer.model_dump(),
                        "prior_session_answers": self._session_history[-8:],
                    }
                raw_dict = self.llm_client.solve_with_failover(
                    llm_context,
                    domain_fallback_fn=lambda _: domain_answer.model_dump(),
                )
                if isinstance(raw_dict, AnswerPayload):
                    ans = raw_dict
                elif isinstance(raw_dict, dict):
                    # Preserve domain_answer's specialized fields (slider_value, ranking_order, code_response)
                    # if stub/LLM returned generic fallback
                    if raw_dict.get("solver_tier") == "stub_fallback" and domain_answer.confidence_score >= 0.85:
                        ans = domain_answer
                    else:
                        merged = domain_answer.model_dump()
                        for k, v in raw_dict.items():
                            if v is not None and v != [] and v != "":
                                merged[k] = v
                        # Ensure slider_value and ranking_order stay populated when needed
                        qtype = str(payload.question_type.value if hasattr(payload.question_type, "value") else payload.question_type).lower()
                        if qtype == "slider" and merged.get("slider_value") is None:
                            if merged.get("text_response"):
                                try:
                                    merged["slider_value"] = int(float(merged["text_response"]))
                                except ValueError:
                                    pass
                        if qtype == "ranking" and not merged.get("ranking_order") and merged.get("selected_option_ids"):
                            merged["ranking_order"] = list(merged["selected_option_ids"])
                        ans = AnswerPayload.model_validate(merged)
                else:
                    ans = domain_answer

                # Strip markdown fences if code response
                if ans.code_response:
                    ans.code_response = TechnicalSolver.strip_markdown_fences(ans.code_response)
                final_ans = ans
            except Exception:
                final_ans = domain_answer

        self._session_history.append({
            "question": payload.prompt_text[:180],
            "selected": ", ".join(final_ans.selected_option_ids) or (final_ans.text_response or "")[:120],
        })
        return final_ans


_default_solver: Optional[AssessmentSolver] = None


def get_solver() -> AssessmentSolver:
    global _default_solver
    if _default_solver is None:
        _default_solver = AssessmentSolver()
    return _default_solver


def solve_question(context: Union[QuestionPayload, Dict[str, Any], Any]) -> AnswerPayload:
    """Module-level contract function: `solver.solve_question(context: QuestionPayload) -> AnswerPayload`."""
    return get_solver().solve_question(context)
