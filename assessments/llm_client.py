"""
Dual-Provider LLM Client with Automatic Failover, Retries, and Budget Enforcement (Feature 13, R3).
Supports:
1. Claude via `anthropic` SDK (`ANTHROPIC_API_KEY`)
2. OpenRouter via `openai` SDK (`OPENROUTER_API_KEY`, base_url="https://openrouter.ai/api/v1")
3. Deterministic Stub / Heuristic fallback when running in test mode or when external APIs fail/exhaust budget.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Type, Union

from pydantic import BaseModel

logger = logging.getLogger("assessments.llm_client")


class LLMProviderError(Exception):
    """Base error raised when an LLM provider fails."""
    pass


class BudgetExceededError(LLMProviderError):
    """Raised when the daily token/cost budget is exceeded."""
    pass


class DualProviderLLMClient:
    """
    Unified LLM client supporting Claude (Anthropic) and OpenRouter (OpenAI-compatible)
    with automatic provider failover, exponential backoff retries, and cost tracking.
    """

    def __init__(
        self,
        provider_preference: Optional[str] = None,
        fallback_enabled: Optional[bool] = None,
        daily_budget_usd: Optional[float] = None,
        stub_provider: Optional[Any] = None,
        max_retries: int = 2,
    ) -> None:
        self.provider_preference = (
            provider_preference
            or os.getenv("LLM_PROVIDER")
            or os.getenv("ASSESSMENT_PRIMARY_PROVIDER", "auto")
        ).strip().lower()
        if fallback_enabled is None:
            self.fallback_enabled = os.getenv("LLM_FALLBACK_ENABLED", "true").lower() in ("true", "1", "yes")
        else:
            self.fallback_enabled = fallback_enabled

        if daily_budget_usd is None:
            try:
                self.daily_budget_usd = float(os.getenv("DAILY_BUDGET_USD", "5.0") or "5.0")
            except ValueError:
                self.daily_budget_usd = 5.0
        else:
            self.daily_budget_usd = daily_budget_usd

        self.max_retries = max_retries
        self.stub_provider = stub_provider
        self.total_cost_usd: float = 0.0
        self.total_tokens: int = 0
        self.call_history: List[Dict[str, Any]] = []
        self.last_provider_used: str = "none"

    def _get_stub(self) -> Optional[Any]:
        if self.stub_provider is not None:
            return self.stub_provider
        if os.getenv("ASSESSMENT_TEST_MODE", "").lower() in ("true", "1", "yes") or self.provider_preference == "stub":
            try:
                from tests.mock_portal.stub_provider import StubLLMProvider
                self.stub_provider = StubLLMProvider()
                return self.stub_provider
            except Exception:
                return None
        return None

    def _is_valid_api_key(self, key: str) -> bool:
        if not key:
            return False
        clean = key.strip()
        if not clean or clean.startswith("mock_") or clean.startswith("your_"):
            return False
        return True

    def get_provider_chain(self) -> List[str]:
        """Determines the ordered list of providers to try based on configuration."""
        if self.provider_preference == "stub":
            return ["stub"]
        if self.provider_preference == "claude":
            return ["claude", "openrouter"] if self.fallback_enabled else ["claude"]
        if self.provider_preference == "openrouter":
            return ["openrouter", "claude"] if self.fallback_enabled else ["openrouter"]
        # Default "auto": Claude primary -> OpenRouter secondary
        return ["claude", "openrouter"] if self.fallback_enabled else ["claude"]

    def check_budget(self) -> None:
        if self.total_cost_usd >= self.daily_budget_usd:
            raise BudgetExceededError(
                f"Daily LLM budget of ${self.daily_budget_usd:.2f} reached (spent ${self.total_cost_usd:.4f})."
            )

    def _call_with_retries(self, fn: Callable[[], Any], provider_name: str) -> Any:
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn()
            except Exception as exc:
                last_err = exc
                status_code = getattr(exc, "status_code", None)
                # Immediate failover on Auth error (401) or Overloaded (529) or RateLimit with long retry_after
                retry_after = getattr(exc, "retry_after", 0.0) or 0.0
                if status_code == 401 or retry_after > 5.0:
                    break
                if attempt < self.max_retries:
                    backoff = min(0.05 * (2 ** (attempt - 1)), 0.5)
                    time.sleep(backoff)
        raise last_err if last_err else LLMProviderError(f"Provider {provider_name} failed")

    def solve_with_failover(
        self,
        question_context: Any,
        domain_fallback_fn: Optional[Callable[[Any], Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Attempts to solve a question through the configured provider chain (Claude -> OpenRouter),
        with automatic failover and final deterministic fallback.
        """
        self.check_budget()
        chain = self.get_provider_chain()
        stub = self._get_stub()
        errors: List[str] = []

        for provider in chain:
            try:
                if provider == "stub" or stub is not None:
                    active_stub = stub or self._get_stub()
                    if active_stub is not None:
                        res = self._call_with_retries(
                            lambda p=provider: active_stub.solve_question(question_context, provider_name=p),
                            provider_name=provider,
                        )
                        self.last_provider_used = provider
                        self._record_call(provider, tokens=0, cost_usd=0.0, status="success")
                        return res

                if provider == "claude":
                    res = self._call_with_retries(
                        lambda: self._call_claude_question(question_context),
                        provider_name="claude",
                    )
                    self.last_provider_used = "claude"
                    return res

                if provider == "openrouter":
                    res = self._call_with_retries(
                        lambda: self._call_openrouter_question(question_context),
                        provider_name="openrouter",
                    )
                    self.last_provider_used = "openrouter"
                    return res

            except Exception as exc:
                logger.warning(f"Provider '{provider}' failed: {type(exc).__name__}: {exc}")
                self._record_call(provider, tokens=0, cost_usd=0.0, status="error", error=str(exc))
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")
                if not self.fallback_enabled:
                    break

        # Final deterministic domain fallback if all LLM providers failed or had no live keys
        if domain_fallback_fn is not None:
            self.last_provider_used = "deterministic_fallback"
            res = domain_fallback_fn(question_context)
            self._record_call("deterministic_fallback", tokens=0, cost_usd=0.0, status="fallback")
            return res

        raise LLMProviderError(f"All providers in chain {chain} failed: {'; '.join(errors)}")

    def ask(
        self,
        system: str,
        user: str,
        schema: Optional[Type[BaseModel]] = None,
        tier: str = "fast",
        agent: str = "assessment_solver",
    ) -> Any:
        """
        Structured prompt completion across the dual-provider chain.
        """
        self.check_budget()
        chain = self.get_provider_chain()
        stub = self._get_stub()
        errors: List[str] = []

        for provider in chain:
            try:
                if provider == "stub" or stub is not None:
                    active_stub = stub or self._get_stub()
                    if active_stub is not None:
                        res = self._call_with_retries(
                            lambda p=provider: active_stub.ask(
                                agent=agent,
                                tier=tier,
                                system=system,
                                user=user,
                                schema=schema,
                                provider_name=p,
                            ),
                            provider_name=provider,
                        )
                        self.last_provider_used = provider
                        self._record_call(provider, tokens=0, cost_usd=0.0, status="success")
                        return res

                if provider == "claude":
                    res = self._call_with_retries(
                        lambda: self._call_claude_raw(system, user, schema=schema, tier=tier),
                        provider_name="claude",
                    )
                    self.last_provider_used = "claude"
                    return res

                if provider == "openrouter":
                    res = self._call_with_retries(
                        lambda: self._call_openrouter_raw(system, user, schema=schema, tier=tier),
                        provider_name="openrouter",
                    )
                    self.last_provider_used = "openrouter"
                    return res

            except Exception as exc:
                logger.warning(f"ask() on provider '{provider}' failed: {exc}")
                self._record_call(provider, tokens=0, cost_usd=0.0, status="error", error=str(exc))
                errors.append(f"{provider}: {exc}")
                if not self.fallback_enabled:
                    break

        raise LLMProviderError(f"All providers failed for ask(): {'; '.join(errors)}")

    def _call_claude_raw(
        self,
        system: str,
        user: str,
        schema: Optional[Type[BaseModel]] = None,
        tier: str = "fast",
    ) -> Any:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not self._is_valid_api_key(api_key):
            raise LLMProviderError("ANTHROPIC_API_KEY is missing or mock.")

        import anthropic

        model = (
            os.getenv("CLAUDE_FAST_MODEL", "claude-3-5-haiku-latest")
            if tier == "fast"
            else (os.getenv("CLAUDE_EXPERT_MODEL") or os.getenv("ASSESSMENT_CLAUDE_MODEL", "claude-3-5-sonnet-latest"))
        )
        client = anthropic.Anthropic(api_key=api_key, max_retries=1, timeout=25.0)
        prompt_user = user
        if schema is not None:
            schema_json = json.dumps(schema.model_json_schema(), indent=2)
            prompt_user = f"{user}\n\nRespond ONLY with valid JSON matching this schema:\n{schema_json}"

        msg = client.messages.create(
            model=model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt_user}],
        )
        text = msg.content[0].text if msg.content else "{}"
        in_tok = getattr(msg.usage, "input_tokens", 100)
        out_tok = getattr(msg.usage, "output_tokens", 100)
        cost = (in_tok * 0.000001) + (out_tok * 0.000005)
        self._record_call("claude", tokens=in_tok + out_tok, cost_usd=cost, status="success")

        if schema is not None:
            cleaned = self._strip_json_fences(text)
            return schema.model_validate_json(cleaned)
        return text

    def _call_openrouter_raw(
        self,
        system: str,
        user: str,
        schema: Optional[Type[BaseModel]] = None,
        tier: str = "fast",
    ) -> Any:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not self._is_valid_api_key(api_key):
            raise LLMProviderError("OPENROUTER_API_KEY is missing or mock.")

        import openai

        model = (
            os.getenv("OPENROUTER_FAST_MODEL", "meta-llama/llama-3.3-70b-instruct")
            if tier == "fast"
            else (os.getenv("OPENROUTER_EXPERT_MODEL") or os.getenv("ASSESSMENT_OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"))
        )
        client = openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            max_retries=1,
            timeout=25.0,
            default_headers={
                "HTTP-Referer": "https://github.com/linkedin-outreach/assessment-solver",
                "X-Title": "LinkedIn Outreach Assessment Solver",
            },
        )
        prompt_user = user
        if schema is not None:
            schema_json = json.dumps(schema.model_json_schema(), indent=2)
            prompt_user = f"{user}\n\nRespond ONLY with valid JSON matching this schema:\n{schema_json}"

        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt_user},
            ],
            temperature=0.0,
        )
        text = resp.choices[0].message.content or "{}"
        tokens = getattr(resp.usage, "total_tokens", 200) if resp.usage else 200
        cost = tokens * 0.000002
        self._record_call("openrouter", tokens=tokens, cost_usd=cost, status="success")

        if schema is not None:
            cleaned = self._strip_json_fences(text)
            return schema.model_validate_json(cleaned)
        return text

    def _call_claude_question(self, context: Any) -> Dict[str, Any]:
        from assessments.contracts import AnswerPayload

        prompt = self._format_question_prompt(context)
        ans = self._call_claude_raw(
            system="You are an expert multi-modal assessment solver. Return an exact AnswerPayload JSON.",
            user=prompt,
            schema=AnswerPayload,
            tier="expert",
        )
        return ans.model_dump()

    def _call_openrouter_question(self, context: Any) -> Dict[str, Any]:
        from assessments.contracts import AnswerPayload

        prompt = self._format_question_prompt(context)
        ans = self._call_openrouter_raw(
            system="You are an expert multi-modal assessment solver. Return an exact AnswerPayload JSON.",
            user=prompt,
            schema=AnswerPayload,
            tier="expert",
        )
        return ans.model_dump()

    @staticmethod
    def _format_question_prompt(context: Any) -> str:
        if isinstance(context, BaseModel):
            return context.model_dump_json(indent=2)
        if isinstance(context, dict):
            return json.dumps(context, indent=2)
        return str(context)

    @staticmethod
    def _strip_json_fences(raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        # Extract outermost JSON object if conversational prose surrounds it
        if not text.startswith("{") and "{" in text and "}" in text:
            start = text.find("{")
            end = text.rfind("}")
            if start < end:
                text = text[start : end + 1]
        return text

    def _record_call(
        self,
        provider: str,
        tokens: int,
        cost_usd: float,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        self.total_tokens += tokens
        self.total_cost_usd = round(self.total_cost_usd + cost_usd, 6)
        self.call_history.append({
            "provider": provider,
            "tokens": tokens,
            "cost_usd": cost_usd,
            "status": status,
            "error": error,
            "timestamp": time.time(),
        })
