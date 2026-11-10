"""Provider adapters.

Different vendors speak different wire formats, so every provider is normalised
behind :class:`ProviderAdapter`. The rest of OPENLAB only sees
``CompletionResult`` objects with text, token usage and error information.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from engine.core.http import build_async_client


class ProviderError(RuntimeError):
    """Raised when a provider call fails, with the HTTP status when known."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body[:2000]


@dataclass
class ChatMessage:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class CompletionResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage_known: bool = False
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None


def _count_tokens_estimate(text: str) -> int:
    """Rough fallback when a provider does not report usage."""
    return max(1, len(text) // 4)


class ProviderAdapter:
    name = "base"
    api_format = "openai-chat"

    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout_s: int = 180,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.extra_headers = extra_headers or {}

    # -- to be overridden ------------------------------------------------
    def build_request(self, messages: list[ChatMessage], json_mode: bool = False) -> tuple[str, dict, dict]:
        raise NotImplementedError

    def parse_response(self, data: dict) -> CompletionResult:
        raise NotImplementedError

    # -- shared ----------------------------------------------------------
    async def complete(
        self,
        messages: list[ChatMessage],
        json_mode: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> CompletionResult:
        import time

        url, headers, payload = self.build_request(messages, json_mode=json_mode)
        started = time.monotonic()
        own_client = client is None
        client = client or build_async_client(timeout=self.timeout_s)
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
        finally:
            if own_client:
                await client.aclose()
        latency = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            raise ProviderError(
                f"{self.name} returned HTTP {resp.status_code}", status=resp.status_code, body=resp.text
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("provider returned non-JSON response", body=resp.text) from exc
        result = self.parse_response(data)
        result.latency_ms = latency
        if not result.usage_known:
            result.prompt_tokens = _count_tokens_estimate(" ".join(m.content for m in messages))
            result.completion_tokens = _count_tokens_estimate(result.text)
            result.total_tokens = result.prompt_tokens + result.completion_tokens
        return result

    async def test_connection(self) -> dict:
        probe = [ChatMessage(role="user", content="Reply with the single word: ok")]
        try:
            result = await self.complete(probe)
        except ProviderError as exc:
            return {"ok": False, "error": str(exc), "status": exc.status}
        return {
            "ok": True,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "reply": result.text.strip()[:120],
            "usage_known": result.usage_known,
        }


class OpenAIChatAdapter(ProviderAdapter):
    """``/chat/completions`` -- OpenAI, DeepSeek and most self-hosted gateways."""

    name = "openai-chat"
    api_format = "openai-chat"

    def build_request(self, messages: list[ChatMessage], json_mode: bool = False):
        url = f"{self.base_url or 'https://api.openai.com/v1'}/chat/completions"
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return url, headers, payload

    def parse_response(self, data: dict) -> CompletionResult:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("response contained no choices", body=json.dumps(data)[:800])
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        usage = data.get("usage") or {}
        return CompletionResult(
            text=(content or "").strip(),
            model=data.get("model") or self.model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            usage_known=bool(usage),
            raw=data,
            finish_reason=choices[0].get("finish_reason"),
        )


class AnthropicAdapter(ProviderAdapter):
    """``/messages`` -- Anthropic Claude."""

    name = "anthropic-messages"
    api_format = "anthropic-messages"

    def build_request(self, messages: list[ChatMessage], json_mode: bool = False):
        url = f"{self.base_url or 'https://api.anthropic.com/v1'}/messages"
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            **self.extra_headers,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        system_parts = [m.content for m in messages if m.role == "system"]
        convo = [m.as_dict() for m in messages if m.role != "system"]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": convo or [{"role": "user", "content": "ping"}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if json_mode:
            payload["system"] = (payload.get("system", "") + "\nRespond with JSON only.").strip()
        return url, headers, payload

    def parse_response(self, data: dict) -> CompletionResult:
        blocks = data.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
        usage = data.get("usage") or {}
        prompt = int(usage.get("input_tokens") or 0)
        completion = int(usage.get("output_tokens") or 0)
        return CompletionResult(
            text=text.strip(),
            model=data.get("model") or self.model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            usage_known=bool(usage),
            raw=data,
            finish_reason=data.get("stop_reason"),
        )


class GeminiAdapter(ProviderAdapter):
    """``/models/{model}:generateContent`` -- Google Gemini."""

    name = "gemini-generate"
    api_format = "gemini-generate"

    def build_request(self, messages: list[ChatMessage], json_mode: bool = False):
        base = self.base_url or "https://generativelanguage.googleapis.com/v1beta"
        url = f"{base}/models/{self.model}:generateContent"
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        system_parts = [m.content for m in messages if m.role == "system"]
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ]
        payload: dict[str, Any] = {
            "contents": contents or [{"role": "user", "parts": [{"text": "ping"}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return url, headers, payload

    def parse_response(self, data: dict) -> CompletionResult:
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError("Gemini returned no candidates", body=json.dumps(data)[:800])
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        usage = data.get("usageMetadata") or {}
        prompt = int(usage.get("promptTokenCount") or 0)
        completion = int(usage.get("candidatesTokenCount") or 0)
        return CompletionResult(
            text=text.strip(),
            model=self.model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=int(usage.get("totalTokenCount") or (prompt + completion)),
            usage_known=bool(usage),
            raw=data,
            finish_reason=candidates[0].get("finishReason"),
        )


ADAPTERS: dict[str, type[ProviderAdapter]] = {
    "openai-chat": OpenAIChatAdapter,
    "anthropic-messages": AnthropicAdapter,
    "gemini-generate": GeminiAdapter,
}

KIND_TO_FORMAT = {
    "openai": "openai-chat",
    "deepseek": "openai-chat",
    "compatible": "openai-chat",
    "anthropic": "anthropic-messages",
    "gemini": "gemini-generate",
}


def build_adapter(
    kind: str,
    api_format: str | None,
    base_url: str | None,
    api_key: str | None,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout_s: int = 180,
    extra_headers: dict[str, str] | None = None,
) -> ProviderAdapter:
    fmt = api_format or KIND_TO_FORMAT.get(kind, "openai-chat")
    adapter_cls = ADAPTERS.get(fmt)
    if adapter_cls is None:
        raise ProviderError(f"unsupported API format: {fmt}")
    return adapter_cls(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        extra_headers=extra_headers,
    )


def extract_json(text: str) -> dict | list:
    """Parse JSON from a model reply that may include prose or code fences."""
    if not text or not text.strip():
        raise ValueError("model returned an empty response")
    stripped = text.strip()
    fence = _FENCE_RE.search(stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    candidate = _first_json_block(stripped)
    if candidate is None:
        raise ValueError("no JSON object found in model response")
    return json.loads(_repair_json(candidate))


import re  # noqa: E402  (kept close to its only use)

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def _first_json_block(text: str) -> str | None:
    start_positions = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not start_positions:
        return None
    start = min(start_positions)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def _repair_json(text: str) -> str:
    """Best-effort cleanup of common LLM JSON mistakes (trailing commas)."""
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    return text
