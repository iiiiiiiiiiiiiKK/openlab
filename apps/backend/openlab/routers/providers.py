"""AI provider management: BYO keys, connectivity tests and usage statistics."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from engine.ai_agent.adapters import build_adapter
from engine.ai_agent.client import ProviderConfig
from engine.core.security import SecretBox
from engine.core.states import PROVIDER_DEFAULTS

from openlab.db import session_scope
from openlab.models import AIProvider
from openlab.schemas import ProviderCreate, ProviderUpdate

router = APIRouter(prefix="/api/providers", tags=["providers"])


def _provider_payload(row: AIProvider, box: SecretBox) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "kind": row.kind,
        "base_url": row.base_url,
        "model": row.model,
        "cheap_model": row.cheap_model,
        "api_format": row.api_format,
        "temperature": row.temperature,
        "max_tokens": row.max_tokens,
        "timeout_s": row.timeout_s,
        "price_in_per_mtok": row.price_in_per_mtok,
        "price_out_per_mtok": row.price_out_per_mtok,
        "enabled": row.enabled,
        "is_default": row.is_default,
        "api_key_masked": box.mask(row.api_key_enc or "") if row.api_key_enc else None,
        "has_api_key": bool(row.api_key_enc),
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "last_check_status": row.last_check_status,
        "last_check_error": row.last_check_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("")
async def list_providers(request: Request) -> dict:
    box = request.app.state.box
    with session_scope() as session:
        rows = list(
            session.scalars(select(AIProvider).order_by(AIProvider.is_default.desc(), AIProvider.name))
        )
        return {
            "items": [_provider_payload(row, box) for row in rows],
            "defaults": PROVIDER_DEFAULTS,
        }


@router.post("")
async def create_provider(request: Request, body: ProviderCreate) -> dict:
    box = request.app.state.box
    defaults = PROVIDER_DEFAULTS.get(body.kind, {})
    with session_scope() as session:
        if body.is_default:
            for row in session.scalars(select(AIProvider)):
                row.is_default = False
        provider = AIProvider(
            name=body.name,
            kind=body.kind,
            base_url=body.base_url or str(defaults.get("base_url") or ""),
            api_key_enc=box.encrypt(body.api_key) if body.api_key else None,
            api_key_hint=box.mask(box.encrypt(body.api_key)) if body.api_key else None,
            model=body.model or str(defaults.get("model") or ""),
            cheap_model=body.cheap_model or str(defaults.get("cheap_model") or ""),
            api_format=body.api_format or str(defaults.get("api_format") or "openai-chat"),
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            timeout_s=body.timeout_s,
            price_in_per_mtok=body.price_in_per_mtok,
            price_out_per_mtok=body.price_out_per_mtok,
            enabled=body.enabled,
            is_default=body.is_default,
            extra_headers=body.extra_headers,
        )
        session.add(provider)
        session.flush()
        return _provider_payload(provider, box)


@router.patch("/{provider_id}")
async def update_provider(request: Request, provider_id: str, body: ProviderUpdate) -> dict:
    box = request.app.state.box
    with session_scope() as session:
        provider = session.get(AIProvider, provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        data = body.model_dump(exclude_unset=True)
        if data.pop("is_default", None):
            for row in session.scalars(select(AIProvider)):
                row.is_default = False
            provider.is_default = True
        api_key = data.pop("api_key", None)
        if api_key:
            provider.api_key_enc = box.encrypt(api_key)
            provider.api_key_hint = box.mask(provider.api_key_enc)
        for key, value in data.items():
            if value is not None and hasattr(provider, key):
                setattr(provider, key, value)
        session.commit()
        return _provider_payload(provider, box)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: str) -> dict:
    with session_scope() as session:
        provider = session.get(AIProvider, provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        session.delete(provider)
        return {"deleted": True}


@router.post("/{provider_id}/test")
async def test_provider(request: Request, provider_id: str) -> dict:
    box = request.app.state.box
    with session_scope() as session:
        provider = session.get(AIProvider, provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
        config = ProviderConfig.from_row(provider, box)
    adapter = build_adapter(
        kind=config.kind,
        api_format=config.api_format,
        base_url=config.base_url,
        api_key=box.decrypt(provider.api_key_enc or ""),
        model=config.model,
        temperature=config.temperature,
        max_tokens=min(config.max_tokens, 256),
        timeout_s=min(config.timeout_s, 60),
        extra_headers=config.extra_headers,
    )
    result = await adapter.test_connection()
    with session_scope() as session:
        row = session.get(AIProvider, provider_id)
        if row is not None:
            row.last_checked_at = datetime.now(timezone.utc)
            row.last_check_status = "ok" if result.get("ok") else "error"
            row.last_check_error = None if result.get("ok") else str(result.get("error"))[:2000]
    return result


@router.get("/usage/summary")
async def usage_summary(request: Request, project_id: str | None = None, days: int | None = None) -> dict:
    client = request.app.state.ai_client()
    try:
        return client.usage_summary(project_id=project_id, days=days)
    finally:
        # The client owns a Session; leaking it exhausted the SQLAlchemy pool and
        # took the whole API down after a handful of usage lookups.
        client.session.close()


@router.get("/usage/records")
async def usage_records(request: Request, limit: int = 100, project_id: str | None = None) -> dict:
    from openlab.models import AIUsageRecord

    with session_scope() as session:
        query = select(AIUsageRecord).order_by(AIUsageRecord.created_at.desc()).limit(limit)
        if project_id:
            query = query.where(AIUsageRecord.project_id == project_id)
        rows = list(session.scalars(query))
        return {
            "items": [
                {
                    "id": r.id,
                    "provider_id": r.provider_id,
                    "project_id": r.project_id,
                    "purpose": r.purpose,
                    "model": r.model,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "cost_usd": r.cost_usd,
                    "usage_is_known": r.usage_is_known,
                    "cost_is_estimate": r.cost_is_estimate,
                    "latency_ms": r.latency_ms,
                    "success": r.success,
                    "error": r.error,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }
