"""Unit tests for provider adapters, JSON extraction and usage accounting."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.ai_agent.adapters import (
    AnthropicAdapter,
    ChatMessage,
    GeminiAdapter,
    OpenAIChatAdapter,
    ProviderError,
    build_adapter,
    extract_json,
)
from engine.ai_agent.client import AIClient, Budget, BudgetExceeded
from engine.core.security import SecretBox


def test_openai_request_shape() -> None:
    adapter = OpenAIChatAdapter(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="gpt-4o-mini",
        temperature=0.1,
        max_tokens=128,
    )
    url, headers, payload = adapter.build_request(
        [ChatMessage(role="user", content="hi")], json_mode=True
    )
    assert url == "https://api.example.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert payload["model"] == "gpt-4o-mini"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0] == {"role": "user", "content": "hi"}


def test_openai_response_parsing() -> None:
    adapter = OpenAIChatAdapter(base_url="", api_key="", model="gpt-4o-mini")
    result = adapter.parse_response(
        {
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )
    assert result.text == "hello"
    assert result.total_tokens == 15
    assert result.usage_known is True


def test_openai_empty_choices_raises() -> None:
    adapter = OpenAIChatAdapter(base_url="", api_key="", model="m")
    with pytest.raises(ProviderError):
        adapter.parse_response({"choices": []})


def test_anthropic_request_shape() -> None:
    adapter = AnthropicAdapter(
        base_url="https://api.anthropic.com/v1", api_key="k", model="claude-3-5-sonnet-latest"
    )
    url, headers, payload = adapter.build_request(
        [ChatMessage(role="system", content="be terse"), ChatMessage(role="user", content="hi")]
    )
    assert url.endswith("/messages")
    assert headers["x-api-key"] == "k"
    assert headers["anthropic-version"]
    assert payload["system"] == "be terse"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_response_parsing() -> None:
    adapter = AnthropicAdapter(base_url="", api_key="", model="claude")
    result = adapter.parse_response(
        {
            "model": "claude",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 7, "output_tokens": 3},
            "stop_reason": "end_turn",
        }
    )
    assert result.text == "hello"
    assert result.total_tokens == 10


def test_gemini_request_and_parsing() -> None:
    adapter = GeminiAdapter(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="k", model="gemini-1.5-pro")
    url, headers, payload = adapter.build_request([ChatMessage(role="user", content="hi")], json_mode=True)
    assert ":generateContent" in url
    assert headers["x-goog-api-key"] == "k"
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    result = adapter.parse_response(
        {
            "candidates": [{"content": {"parts": [{"text": "yo"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2, "totalTokenCount": 6},
        }
    )
    assert result.text == "yo"
    assert result.total_tokens == 6


def test_build_adapter_selects_format() -> None:
    adapter = build_adapter("anthropic", None, None, "k", "m")
    assert isinstance(adapter, AnthropicAdapter)
    assert isinstance(build_adapter("openai", None, None, "k", "m"), OpenAIChatAdapter)
    assert isinstance(build_adapter("gemini", None, None, "k", "m"), GeminiAdapter)
    with pytest.raises(ProviderError):
        build_adapter("openai", "unsupported-format", None, "k", "m")


def test_extract_json_variants() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('Sure! Here it is: {"a": 3, "b": [1,2]} hope it helps') == {"a": 3, "b": [1, 2]}
    assert extract_json('[{"a": 4}]') == [{"a": 4}]
    assert extract_json('{"a": 5,}') == {"a": 5}


def test_extract_json_failure_modes() -> None:
    with pytest.raises(ValueError):
        extract_json("")
    with pytest.raises(ValueError):
        extract_json("no json here at all")


def test_budget_limits() -> None:
    budget = Budget(max_requests=2, max_tokens=1000, max_usd=0.0)
    budget.check()
    budget.requests_used = 2
    with pytest.raises(BudgetExceeded):
        budget.check()
    budget2 = Budget(max_tokens=10)
    budget2.tokens_used = 10
    with pytest.raises(BudgetExceeded):
        budget2.check(projected_tokens=5)
    budget3 = Budget(max_usd=0.01)
    budget3.usd_used = 0.02
    with pytest.raises(BudgetExceeded):
        budget3.check()


def test_usage_summary_from_records(db, box: SecretBox, tmp_path: Path) -> None:
    from openlab.db import session_scope
    from openlab.models import AIProvider, AIUsageRecord

    with session_scope() as session:
        provider = AIProvider(
            name="test",
            kind="openai",
            base_url="https://api.example.com/v1",
            api_key_enc=box.encrypt("sk-test"),
            model="gpt-4o-mini",
            api_format="openai-chat",
            enabled=True,
            is_default=True,
        )
        session.add(provider)
        session.flush()
        session.add(
            AIUsageRecord(
                provider_id=provider.id,
                purpose="analysis",
                model="gpt-4o-mini",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                cost_usd=0.001,
                usage_is_known=True,
            )
        )
        session.add(
            AIUsageRecord(
                provider_id=provider.id,
                purpose="repair",
                model="gpt-4o-mini",
                total_tokens=0,
                usage_is_known=False,
                success=False,
                error="boom",
            )
        )

    from openlab.db import session_factory

    client = AIClient(session_factory()(), box)
    summary = client.usage_summary()
    assert summary["requests"] == 2
    assert summary["tokens"] == 150
    assert summary["records_with_unknown_usage"] == 1
    assert summary["by_purpose"]["analysis"]["tokens"] == 150
    client.session.close()


def test_provider_resolution_requires_enabled(db, box: SecretBox) -> None:
    from engine.ai_agent.client import NoProviderConfigured
    from openlab.db import session_factory, session_scope
    from openlab.models import AIProvider

    client = AIClient(session_factory()(), box)
    with pytest.raises(NoProviderConfigured):
        client.resolve()
    with session_scope() as session:
        session.add(
            AIProvider(
                name="disabled",
                kind="openai",
                model="gpt-4o-mini",
                api_format="openai-chat",
                enabled=False,
            )
        )
    with pytest.raises(NoProviderConfigured):
        client.resolve()
    client.session.close()


def test_usage_record_is_written_on_purpose(db, box: SecretBox) -> None:
    from openlab.db import session_factory, session_scope
    from openlab.models import AIProvider, AIUsageRecord

    with session_scope() as session:
        session.add(
            AIProvider(
                name="p",
                kind="openai",
                model="m",
                api_format="openai-chat",
                enabled=True,
                is_default=True,
            )
        )
    client = AIClient(session_factory()(), box)
    provider, config = client.resolve()
    assert config.model == "m"
    client.session.close()
    with session_scope() as session:
        assert session.query(AIUsageRecord).count() == 0
