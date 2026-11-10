"""Shared HTTP client construction.

The environment frequently carries a ``no_proxy`` list in the bracketed IPv6 form
(``localhost,127.0.0.1,::1,[::1]``). httpx parses every entry as a URL pattern, and
``[::1]`` makes that parse fail with ``InvalidURL: Invalid port: ':1]'`` — raised
while constructing the client itself, so *every* outbound request (GitHub import,
model calls, health checks) died before it was even attempted.

This module normalises those entries once and is the single place clients are built.
"""

from __future__ import annotations

import os
import re

import httpx

_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy")
# Bracketed or bare loopback/spec entries that httpx cannot turn into a URL pattern.
_BRACKETED = re.compile(r"\[[^\]]*\]")
_SPECIAL = {"::1", "0.0.0.0", "::"}


def sanitize_no_proxy(value: str) -> str:
    """Return a no_proxy list httpx can parse, keeping the same exclusions."""
    entries: list[str] = []
    for raw in re.split(r"[,\s]+", value or ""):
        entry = raw.strip()
        if not entry:
            continue
        entry = _BRACKETED.sub("", entry).strip()
        if not entry:
            continue
        if entry in _SPECIAL:
            # httpx cannot express an IPv6 literal here; loopback is excluded by the
            # plain 127.0.0.1/localhost entries that accompany it in practice.
            continue
        if entry not in entries:
            entries.append(entry)
    return ",".join(entries)


def normalize_proxy_environment() -> None:
    """Sanitise NO_PROXY/low-case no_proxy in place (idempotent)."""
    for key in _PROXY_ENV_KEYS:
        current = os.environ.get(key)
        if current is None:
            continue
        cleaned = sanitize_no_proxy(current)
        if cleaned != current:
            os.environ[key] = cleaned


def build_client(**kwargs) -> httpx.Client:
    """Construct an httpx Client that cannot die on a malformed no_proxy list."""
    normalize_proxy_environment()
    return httpx.Client(**kwargs)


def build_async_client(**kwargs) -> httpx.AsyncClient:
    """Construct an httpx AsyncClient that cannot die on a malformed no_proxy list."""
    normalize_proxy_environment()
    return httpx.AsyncClient(**kwargs)
