"""
Deterministic Test Stub LLM Provider.
Provides zero-token, zero-latency ground-truth answering, graceful heuristic fallbacks,
and multi-tier failure simulation for testing the Dual-Provider failover chain.
"""

from __future__ import annotations

import contextlib
import enum
import hashlib
import json
import logging
import pathlib
import re
import time
from typing import Any, Dict, List, Optional, Type, Union

from pydantic import BaseModel

logger = logging.getLogger("stub_provider")

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES_DIR = HERE / "fixtures"


class SimulationMode(str, enum.Enum):
    NORMAL = "normal"
    RATE_LIMIT_429 = "rate_limit_429"
    TIMEOUT = "timeout"
    AUTH_ERROR_401 = "auth_error_401"
    SERVER_ERROR_500 = "server_error_500"
    SERVER_OVERLOADED_529 = "server_overloaded_529"
    CORRUPTED_JSON = "corrupted_json"
    SLOW_LATENCY = "slow_latency"


class StubProviderError(Exception):
    """Base error for stub simulation."""
    pass


class StubRateLimitError(StubProviderError):
    def __init__(self, message: str = "HTTP 429: Rate limit exceeded", retry_after: float = 1.0):
        super().__init__(message)
        self.status_code = 429
        self.retry_after = retry_after


class StubTimeoutError(StubProviderError):
    def __init__(self, message: str = "Request timed out after 30.0s"):
        super().__init__(message)


class StubAuthError(StubProviderError):
    def __init__(self, message: str = "HTTP 401: Invalid API key"):
        super().__init__(message)
        self.status_code = 401


class StubServerError(StubProviderError):
    def __init__(self, status_code: int = 500, message: str = "HTTP 500: Internal server error"):
        super().__init__(message)
        self.status_code = status_code


class StubLLMProvider:
    """
    Zero-token deterministic LLM provider for unit and E2E assessment testing.
    """

    def __init__(self, fixtures_dir: Optional[pathlib.Path] = None):
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR
        self.fixtures_by_id: Dict[str, Dict[str, Any]] = {}
        self.fixtures_by_prompt_hash: Dict[str, Dict[str, Any]] = {}
        self.call_count = 0
        self.usage_records: List[Dict[str, Any]] = []

        # Simulation state
        self._mode: SimulationMode = SimulationMode.NORMAL
        self._target_provider: Optional[str] = None  # None = all, "claude", or "openrouter"
        self._failure_budget: int = 0
        self._injected_delay: float = 0.0

        self.reload_fixtures()

    def reload_fixtures(self) -> None:
        """Loads and indexes all JSON fixtures from the fixtures directory."""
        self.fixtures_by_id.clear()
        self.fixtures_by_prompt_hash.clear()

        if not self.fixtures_dir.exists():
            return

        for path in self.fixtures_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                fixtures_list = data.get("fixtures", [])
                for item in fixtures_list:
                    qid = item.get("question_id")
                    if qid:
                        self.fixtures_by_id[qid] = item

                    prompt = item.get("prompt", "")
                    if prompt:
                        phash = self._hash_prompt(prompt)
                        self.fixtures_by_prompt_hash[phash] = item
            except Exception as e:
                logger.warning(f"Error loading fixture file {path}: {e}")

    @staticmethod
    def _hash_prompt(prompt: str) -> str:
        """Normalizes and hashes prompt text for prompt-based fixture lookups."""
        normalized = re.sub(r"\s+", " ", prompt).strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def set_simulation_mode(
        self,
        mode: SimulationMode,
        target_provider: Optional[str] = None,
        fail_count: int = 1,
        injected_delay: float = 0.0,
    ) -> None:
        """Configures failure simulation parameters."""
        self._mode = mode
        self._target_provider = target_provider.lower() if target_provider else None
        self._failure_budget = fail_count
        self._injected_delay = injected_delay

    def reset(self) -> None:
        """Resets simulation modes, failure budgets, and call counters."""
        self._mode = SimulationMode.NORMAL
        self._target_provider = None
        self._failure_budget = 0
        self._injected_delay = 0.0
        self.call_count = 0
        self.usage_records.clear()

    @contextlib.contextmanager
    def simulate_rate_limit(self, provider: Optional[str] = None, count: int = 1):
        """Context manager to simulate HTTP 429 rate limit errors."""
        prev_mode, prev_target, prev_budget = self._mode, self._target_provider, self._failure_budget
        self.set_simulation_mode(SimulationMode.RATE_LIMIT_429, target_provider=provider, fail_count=count)
        try:
            yield self
        finally:
            self._mode, self._target_provider, self._failure_budget = prev_mode, prev_target, prev_budget

    @contextlib.contextmanager
    def simulate_timeout(self, provider: Optional[str] = None, count: int = 1):
        """Context manager to simulate connection timeouts."""
        prev_mode, prev_target, prev_budget = self._mode, self._target_provider, self._failure_budget
        self.set_simulation_mode(SimulationMode.TIMEOUT, target_provider=provider, fail_count=count)
        try:
            yield self
        finally:
            self._mode, self._target_provider, self._failure_budget = prev_mode, prev_target, prev_budget

    @contextlib.contextmanager
    def simulate_auth_error(self, provider: Optional[str] = None):
        """Context manager to simulate HTTP 401 authentication errors."""
        prev_mode, prev_target, prev_budget = self._mode, self._target_provider, self._failure_budget
        self.set_simulation_mode(SimulationMode.AUTH_ERROR_401, target_provider=provider, fail_count=999)
        try:
            yield self
        finally:
            self._mode, self._target_provider, self._failure_budget = prev_mode, prev_target, prev_budget

    def _check_simulation(self, current_provider: Optional[str] = None) -> None:
        """Evaluates whether to trigger an injected failure."""
        if self._injected_delay > 0:
            time.sleep(self._injected_delay)

        if self._mode == SimulationMode.NORMAL:
            return

        if self._target_provider and current_provider:
            if self._target_provider != current_provider.lower():
                return  # Simulation targeted to a different provider

        if self._failure_budget > 0:
            self._failure_budget -= 1
            if self._mode == SimulationMode.RATE_LIMIT_429:
                raise StubRateLimitError()
            elif self._mode == SimulationMode.TIMEOUT:
                raise StubTimeoutError()
            elif self._mode == SimulationMode.AUTH_ERROR_401:
                raise StubAuthError()
            elif self._mode == SimulationMode.SERVER_ERROR_500:
                raise StubServerError(500)
            elif self._mode == SimulationMode.SERVER_OVERLOADED_529:
                raise StubServerError(529, "HTTP 529: Claude Overloaded")

    # -------------------------------------------------------------------------
    # High-Level Assessment Solver Interface (PROJECT.md R3 Interface)
    # -------------------------------------------------------------------------
    def solve_question(self, context: Any, provider_name: str = "stub") -> Dict[str, Any]:
        """
        Solves an assessment question deterministically.
        Context can be a QuestionPayload (Pydantic model) or a dict.
        Returns an AnswerPayload dict.
        """
        self.call_count += 1
        self._check_simulation(current_provider=provider_name)

        # Extract attributes from context
        qid = getattr(context, "question_id", None) or (
            context.get("question_id") if isinstance(context, dict) else ""
        )
        prompt = getattr(context, "prompt_text", None) or (
            context.get("prompt_text") if isinstance(context, dict) else (
                context.get("prompt") if isinstance(context, dict) else ""
            )
        )
        category = getattr(context, "category", "") or (
            context.get("category", "") if isinstance(context, dict) else ""
        )
        qtype = getattr(context, "question_type", "") or (
            context.get("question_type", "") if isinstance(context, dict) else ""
        )
        options = getattr(context, "options", []) or (
            context.get("options", []) if isinstance(context, dict) else []
        )

        # 1. Lookup in ground-truth fixtures
        fixture = None
        if qid and qid in self.fixtures_by_id:
            fixture = self.fixtures_by_id[qid]
        elif prompt:
            phash = self._hash_prompt(prompt)
            if phash in self.fixtures_by_prompt_hash:
                fixture = self.fixtures_by_prompt_hash[phash]

        if fixture and "ground_truth_payload" in fixture:
            gt = fixture["ground_truth_payload"].copy()
            gt["question_id"] = qid or fixture.get("question_id", "")
            self._record_stub_usage(gt["question_id"], "ground_truth")
            return gt

        # 2. Unmapped Question Fallback
        fallback_answer = self._generate_heuristic_fallback(qid, prompt, category, qtype, options)
        self._record_stub_usage(qid, "heuristic_fallback")
        return fallback_answer

    def _generate_heuristic_fallback(
        self,
        qid: str,
        prompt: str,
        category: str,
        qtype: str,
        options: List[Any],
    ) -> Dict[str, Any]:
        """Deterministic heuristic fallback when no fixture ground truth matches."""
        selected_option_ids: List[str] = []
        text_response: Optional[str] = None
        code_response: Optional[str] = None
        reasoning = f"Deterministic fallback for unmapped question {qid}"

        category_str = str(category).lower()
        qtype_str = str(qtype).lower()

        # Extract option IDs if present
        opt_ids = []
        for opt in options:
            if isinstance(opt, dict) and "id" in opt:
                opt_ids.append(opt["id"])
            elif hasattr(opt, "id"):
                opt_ids.append(opt.id)

        if "choice" in qtype_str:
            selected_option_ids = [opt_ids[0]] if opt_ids else ["A"]
        elif "code" in qtype_str:
            code_response = "def solution():\n    return 0\n"
        elif "math" in category_str or "logic" in category_str:
            nums = re.findall(r"\b\d+\b", prompt)
            text_response = str(sum(int(n) for n in nums[:2])) if len(nums) >= 2 else "42"
        elif "behavioral" in category_str:
            text_response = (
                "At university and in prior automation projects, I led the development "
                "of resilient Python automation pipelines. I defined measurable milestones, validated "
                "edge-case data, and delivered 100% data integrity with positive operational impact."
            )
        else:
            text_response = "Confirmed and verified."

        return {
            "question_id": qid,
            "selected_option_ids": selected_option_ids,
            "text_response": text_response,
            "code_response": code_response,
            "confidence_score": 0.5,
            "reasoning": reasoning,
            "solver_tier": "stub_fallback",
        }

    # -------------------------------------------------------------------------
    # Low-Level Structured LLM Client Interface (llm_client.ask compatibility)
    # -------------------------------------------------------------------------
    def ask(
        self,
        agent: str = "assessment_agent",
        tier: str = "fast",
        system: str = "",
        user: str = "",
        schema: Optional[Type[BaseModel]] = None,
        provider_name: str = "stub",
        **kwargs: Any,
    ) -> Any:
        """
        Emulates structured output calls for llm_client.
        Returns a validated instance of schema, or mock dict/text.
        """
        self.call_count += 1
        self._check_simulation(current_provider=provider_name)

        if self._mode == SimulationMode.CORRUPTED_JSON:
            raise json.JSONDecodeError("Injected corrupted JSON", "{invalid_json:", 0)

        # Check prompt against fixture ground truth
        phash = self._hash_prompt(user)
        fixture = self.fixtures_by_prompt_hash.get(phash)

        if fixture and schema:
            gt_payload = fixture.get("ground_truth_payload", {})
            try:
                return schema.model_validate(gt_payload)
            except Exception:
                pass

        if schema:
            try:
                return schema.model_validate({})
            except Exception:
                dummy_fields = {}
                for name, field in schema.model_fields.items():
                    annotation = str(field.annotation).lower()
                    if "str" in annotation:
                        dummy_fields[name] = "stub_answer"
                    elif "int" in annotation:
                        dummy_fields[name] = 1
                    elif "float" in annotation:
                        dummy_fields[name] = 1.0
                    elif "list" in annotation:
                        dummy_fields[name] = []
                    elif "bool" in annotation:
                        dummy_fields[name] = True
                    else:
                        dummy_fields[name] = None
                return schema.model_validate(dummy_fields)

        return {"status": "ok", "response": "Deterministic test response"}

    def _record_stub_usage(self, qid: str, tier: str) -> None:
        self.usage_records.append({
            "question_id": qid,
            "tier": tier,
            "tokens": 0,
            "cost_usd": 0.0,
        })
