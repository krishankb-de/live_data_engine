"""Shared async HTTP client factory.

Why this module exists:
  - `truststore` makes Python's SSL verification use the OS-native trust store
    (macOS Keychain on the dev box, public CAs on the demo laptop). Works under
    a corporate TLS-intercepting proxy AND on a regular café WiFi without code
    changes.
  - Every outbound HTTP request in this project should go through `make_client()`
    so we have a single source of truth for User-Agent, timeout, redirect policy,
    and cert verification.
"""

from __future__ import annotations

import ssl
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

try:
    import truststore  # macOS keychain / corporate-CA aware verification
    _HAS_TRUSTSTORE = True
except ImportError:  # pragma: no cover — optional dependency
    _HAS_TRUSTSTORE = False

from config import settings


def _ssl_context() -> ssl.SSLContext | bool:
    """SSLContext that trusts whatever the OS trusts (incl. corporate CAs).

    Falls back to httpx's default verification (`verify=True`) when truststore
    isn't installed.
    """
    if _HAS_TRUSTSTORE:
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return True


@asynccontextmanager
async def make_client(
    *,
    extra_headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a configured `httpx.AsyncClient`.

    Defaults pulled from `settings`. Always async, always follow_redirects.
    """
    headers = {"User-Agent": settings.user_agent}
    if extra_headers:
        headers.update(extra_headers)

    client = httpx.AsyncClient(
        headers=headers,
        verify=_ssl_context(),
        timeout=timeout or settings.http_timeout_seconds,
        follow_redirects=True,
    )
    try:
        yield client
    finally:
        await client.aclose()
