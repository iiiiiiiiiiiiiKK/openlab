"""Provider registry, usage accounting and cost control.

Only planning, source comprehension and complex repair calls hit a model. Every
call is written to ``ai_usage_records`` with token counts, latency and an
explicit ``usage_is_known`` / ``cost_is_estimate`` flag -- OPENLAB never presents
guessed numbers as measured ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.ai_agent.adapters import (
    ChatMessage,
    CompletionResult,
    ProviderAdapter,
    ProviderError,
    build_adapter,
    extract_json,
)
from engine.core.security import SecretBox, redact

from openlab.models import AIProvider, AIUsageRecord


class NoProviderConfigured(RuntimeError):
    """Raised when a task needs a model but no provider is enabled."""


class BudgetExceeded(RuntimeError):
    """Raised when a task would exceed its configured budget or call cap."""


@dataclass
class ProviderConfig:
    id: str
    name: str
    kind: str
    base_url: str | None
    model: str
    cheap_model: str | None
    api_format: str
    temperature: float
    max_tokens: int
    timeout_s: int
    price_in_per_mtok: float
    price_out_per_mtok: float
    extra_headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: AIProvider, box: SecretBox) -> "ProviderConfig":
        api_key = box.decrypt(row.api_key_enc or "")
        return cls(
            id=row.id,
            name=row.name,
            kind=row.kind,
            base_url=row.base_url,
            model=row.model or "",
            cheap_model=row.cheap_model,
            api_format=row.api_format or "openai-chat",
            temperature=row.temperature,
            max_tokens=row.max_tokens,
            timeout_s=row.timeout_s,
            price_in_per_mtok=row.price_in_per_mtok,
            price_out_per_mtok=row.price_out_per_mtok,
            extra_headers={k: str(v) for k, v in (row.extra_headers or {}).items()},
        )


class AIClient:
    """A thin facade that resolves a provider, calls it and records usage."""

    def __init__(self, session: Session, box: SecretBox) -> None:
        self.session = session
        self.box = box

    def list_providers(self) -> list[AIProvider]:
        return list(
            self.session.scalars(select(AIProvider).order_by(AIProvider.is_default.desc(), AIProvider.name))
        )

    def resolve(self, provider_id: str | None = None, prefer_cheap: bool = False) -> tuple[AIProvider, ProviderConfig]:
        rows = [p for p in self.list_providers() if p.enabled]
        if not rows:
            raise NoProviderConfigured(
                "No AI provider is enabled. Add one under Model Providers (OpenAI, Anthropic, "
                "Gemini, DeepSeek or any OpenAI-compatible endpoint)."
            )
        if provider_id:
            match = next((p for p in rows if p.id == provider_id), None)
            if match is None:
                raise NoProviderConfigured(f"provider {provider_id} is missing or disabled")
        else:
            match = next((p for p in rows if p.is_default), rows[0])
        config = ProviderConfig.from_row(match, self.box)
        if prefer_cheap and config.cheap_model:
            config.model = config.cheap_model
        return match, config

    def adapter_for(self, config: ProviderConfig) -> ProviderAdapter:
        row = self.session.get(AIProvider, config.id)
        api_key = self.box.decrypt(row.api_key_enc or "") if row else ""
        return build_adapter(
            kind=config.kind,
            api_format=config.api_format,
            base_url=config.base_url,
            api_key=api_key,
            model=config.model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout_s=config.timeout_s,
            extra_headers=config.extra_headers,
        )

    async def complete(
        self,
        messages: list[ChatMessage],
        purpose: str = "analysis",
        project_id: str | None = None,
        task_id: str | None = None,
        provider_id: str | None = None,
        prefer_cheap: bool = False,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        row, config = self.resolve(provider_id=provider_id, prefer_cheap=prefer_cheap)
        if max_tokens:
            config.max_tokens = max_tokens
        adapter = self.adapter_for(config)
        error: str | None = None
        result: CompletionResult | None = None
        try:
            result = await adapter.complete(messages, json_mode=json_mode)
            return result
        except ProviderError as exc:
            error = str(exc)
            raise
        finally:
            self._record_usage(
                provider=row,
                config=config,
                result=result,
                purpose=purpose,
                project_id=project_id,
                task_id=task_id,
                error=error,
            )

    async def complete_json(
        self,
        messages: list[ChatMessage],
        purpose: str = "analysis",
        project_id: str | None = None,
        task_id: str | None = None,
        provider_id: str | None = None,
        prefer_cheap: bool = False,
        schema_hint: str | None = None,
    ) -> dict[str, Any]:
        if schema_hint:
            messages = list(messages) + [
                ChatMessage(role="user", content=f"Respond with JSON only. Schema hint:\n{schema_hint}")
            ]
        result = await self.complete(
            messages,
            purpose=purpose,
            project_id=project_id,
            task_id=task_id,
            provider_id=provider_id,
            prefer_cheap=prefer_cheap,
            json_mode=True,
        )
        parsed = extract_json(result.text)
        if not isinstance(parsed, dict):
            raise ProviderError("expected a JSON object from the model", body=str(parsed)[:800])
        return parsed

    def _record_usage(
        self,
        provider: AIProvider,
        config: ProviderConfig,
        result: CompletionResult | None,
        purpose: str,
        project_id: str | None,
        task_id: str | None,
        error: str | None,
    ) -> None:
        prompt = result.prompt_tokens if result else 0
        completion = result.completion_tokens if result else 0
        known = bool(result and result.usage_known)
        cost = 0.0
        if known:
            cost = (
                prompt / 1_000_000 * config.price_in_per_mtok
                + completion / 1_000_000 * config.price_out_per_mtok
            )
        record = AIUsageRecord(
            provider_id=provider.id,
            project_id=project_id,
            task_id=task_id,
            purpose=purpose,
            model=(result.model if result else config.model),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=(prompt + completion),
            requests=1,
            cost_usd=round(cost, 6),
            cost_is_estimate=True,
            usage_is_known=known,
            latency_ms=result.latency_ms if result else None,
            success=error is None,
            error=redact(error)[:2000] if error else None,
        )
        self.session.add(record)
        self.session.commit()

    # -- reporting -------------------------------------------------------
    def usage_summary(self, project_id: str | None = None, days: int | None = None) -> dict:
        query = select(AIUsageRecord)
        if project_id:
            query = query.where(AIUsageRecord.project_id == project_id)
        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            query = query.where(AIUsageRecord.created_at >= cutoff)
        records = list(self.session.scalars(query))
        by_provider: dict[str, dict] = {}
        by_purpose: dict[str, dict] = {}
        total_cost = 0.0
        unknown_usage = 0
        for record in records:
            prov = by_provider.setdefault(
                record.provider_id or "unknown",
                {"requests": 0, "tokens": 0, "cost_usd": 0.0, "failures": 0},
            )
            prov["requests"] += record.requests
            prov["tokens"] += record.total_tokens
            prov["cost_usd"] += record.cost_usd
            if not record.success:
                prov["failures"] += 1
            purpose = by_purpose.setdefault(
                record.purpose, {"requests": 0, "tokens": 0, "cost_usd": 0.0}
            )
            purpose["requests"] += 1
            purpose["tokens"] += record.total_tokens
            purpose["cost_usd"] += record.cost_usd
            total_cost += record.cost_usd
            if not record.usage_is_known:
                unknown_usage += 1
        return {
            "requests": sum(p["requests"] for p in by_provider.values()),
            "tokens": sum(p["tokens"] for p in by_provider.values()),
            "cost_usd": round(total_cost, 6),
            "cost_is_estimate": True,
            "records_with_unknown_usage": unknown_usage,
            "by_provider": {
                k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in by_provider.items()
            },
            "by_purpose": {
                k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in by_purpose.items()
            },
        }

    def usage_totals(self) -> dict:
        total = self.session.scalar(select(func.count(AIUsageRecord.id))) or 0
        tokens = self.session.scalar(select(func.coalesce(func.sum(AIUsageRecord.total_tokens), 0))) or 0
        cost = self.session.scalar(select(func.coalesce(func.sum(AIUsageRecord.cost_usd), 0.0))) or 0.0
        return {"records": int(total), "tokens": int(tokens), "cost_usd": round(float(cost), 6)}


@dataclass
class Budget:
    """Per-task guard rails: max requests, max tokens, max USD."""

    max_requests: int = 12
    max_tokens: int = 400_000
    max_usd: float = 0.0  # 0 disables the cost cap
    requests_used: int = 0
    tokens_used: int = 0
    usd_used: float = 0.0

    def check(self, projected_tokens: int = 0, projected_usd: float = 0.0) -> None:
        if self.requests_used + 1 > self.max_requests:
            raise BudgetExceeded(
                f"request cap reached ({self.max_requests} calls); increase the task budget in Settings"
            )
        if self.tokens_used + projected_tokens > self.max_tokens:
            raise BudgetExceeded(f"token cap reached ({self.max_tokens})")
        if self.max_usd and self.usd_used + projected_usd > self.max_usd:
            raise BudgetExceeded(f"cost cap reached (${self.max_usd:.2f})")

    def consume(self, result: CompletionResult, price_in: float = 0.0, price_out: float = 0.0) -> None:
        self.requests_used += 1
        self.tokens_used += result.total_tokens
        if result.usage_known:
            self.usd_used += (
                result.prompt_tokens / 1_000_000 * price_in
                + result.completion_tokens / 1_000_000 * price_out
            )

    def as_dict(self) -> dict:
        return {
            "max_requests": self.max_requests,
            "max_tokens": self.max_tokens,
            "max_usd": self.max_usd,
            "requests_used": self.requests_used,
            "tokens_used": self.tokens_used,
            "usd_used": round(self.usd_used, 6),
        }


def export_usage_json(client: AIClient, project_id: str | None = None) -> str:
    return json.dumps(client.usage_summary(project_id=project_id), indent=2, ensure_ascii=False)
