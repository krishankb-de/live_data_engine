"""Auth: X-API-Key (service) or Bearer JWT (user).

Returns Principal used for attribution on accept/reject.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

import jwt
from fastapi import HTTPException, Request, status


@dataclass
class Principal:
    kind: Literal["service", "user"]
    user_id: Optional[str] = None
    api_key_label: Optional[str] = None

    def label(self) -> str:
        if self.kind == "user":
            return f"user:{self.user_id}"
        return f"service:{self.api_key_label or 'api'}"


def require_auth(request: Request) -> Principal:
    """FastAPI dependency. Raises 401 on failure."""
    api_key = request.headers.get("X-API-Key", "")
    bearer = request.headers.get("Authorization", "")

    expected_key = os.environ.get("API_KEY", "")
    jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")

    # --- service key ---
    if api_key and expected_key:
        if _const_compare(api_key, expected_key):
            return Principal(kind="service", api_key_label="api")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    # --- Bearer JWT ---
    if bearer.startswith("Bearer "):
        token = bearer[7:]
        if not jwt_secret:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT secret not configured",
            )
        try:
            payload = jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            return Principal(kind="user", user_id=payload.get("sub"))
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing credentials: provide X-API-Key or Authorization: Bearer <jwt>",
    )


def _const_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())
