"""Bearer service-credential helpers for narrow System Data APIs."""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException


def required_service_token(configured: str | None, *, environment_name: str) -> str:
    token = (configured or os.environ.get(environment_name) or "").strip()
    if len(token) < 24:
        raise RuntimeError(f"{environment_name} must contain at least 24 characters")
    return token


def authorize_service(authorization: str | None, expected_token: str) -> None:
    authorization = authorization or ""
    scheme, separator, supplied_token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied_token:
        raise HTTPException(status_code=401, detail="Bearer service credential required")
    if not hmac.compare_digest(supplied_token, expected_token):
        raise HTTPException(status_code=403, detail="invalid service credential")
