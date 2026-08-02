"""轻量级身份认证与密码工具。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

TOKEN_TTL_SECONDS = int(os.getenv("INVENTORY_TOKEN_TTL_SECONDS", "28800"))


def _secret() -> bytes:
    value = os.getenv("INVENTORY_SECRET_KEY", "")
    if not value:
        raise RuntimeError("未配置 INVENTORY_SECRET_KEY，请在 .env 中配置至少32位随机字符串")
    if len(value) < 32:
        raise RuntimeError("INVENTORY_SECRET_KEY 长度不足，请配置至少32位随机字符串")
    return value.encode("utf-8")


def require_secret_configured() -> None:
    _secret()


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2_sha256$200000${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
        return hmac.compare_digest(digest.hex(), expected)
    except (TypeError, ValueError):
        return False


def create_access_token(user: dict[str, Any]) -> str:
    payload = {
        "sub": int(user["id"]),
        "role": user["role"],
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(_secret(), body, hashlib.sha256).digest()
    return f"{body.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        body_text, signature_text = token.split(".", 1)
        body = body_text.encode()
        expected = hmac.new(_secret(), body, hashlib.sha256).digest()
        actual = base64.urlsafe_b64decode(signature_text + "=" * (-len(signature_text) % 4))
        if not hmac.compare_digest(actual, expected):
            return None
        raw = base64.urlsafe_b64decode(body_text + "=" * (-len(body_text) % 4))
        payload = json.loads(raw)
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
