"""公网后台只接受指定 Cloudflare Access 应用签发、且邮箱获准的身份。"""

from __future__ import annotations

import hashlib
import os
import re
import time
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKSetError
from starlette.requests import Request

MAX_ASSERTION_LENGTH = 16384
_HOST = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
_TEAM_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com\Z")
_EMAIL = re.compile(r"[^@\s,]+@[^@\s,]+\.[^@\s,]+\Z")


def remote_admin_enabled() -> bool:
    value = os.getenv("INVENTORY_REMOTE_ADMIN_ENABLED", "0").strip()
    if value not in {"0", "1"}:
        raise RuntimeError("INVENTORY_REMOTE_ADMIN_ENABLED 只能设置为 0 或 1")
    return value == "1"


def remote_admin_host() -> str:
    return os.getenv("INVENTORY_ADMIN_HOST", "").strip().lower()


def _remote_config() -> tuple[str, str, str, frozenset[str]]:
    host = remote_admin_host()
    if not _HOST.fullmatch(host):
        raise RuntimeError("请将 INVENTORY_ADMIN_HOST 配置为后台专用域名，不含协议、端口或路径")
    raw_team = os.getenv("CLOUDFLARE_ACCESS_TEAM_DOMAIN", "").strip()
    try:
        team = urlsplit(raw_team)
        valid_team = (team.scheme == "https" and _TEAM_HOST.fullmatch(team.hostname or "")
                      and team.port in (None, 443) and not team.username and not team.password
                      and team.path in ("", "/") and not team.query and not team.fragment)
    except ValueError:
        valid_team = False
    if not valid_team:
        raise RuntimeError("CLOUDFLARE_ACCESS_TEAM_DOMAIN 必须是官方 HTTPS Cloudflare Access team 域名")
    issuer = f"https://{team.hostname}"
    audience = os.getenv("CLOUDFLARE_ACCESS_AUD", "").strip()
    if not audience or len(audience) > 1024 or any(c.isspace() for c in audience):
        raise RuntimeError("请配置有效的 CLOUDFLARE_ACCESS_AUD")
    emails = frozenset(value.strip().lower() for value in os.getenv("INVENTORY_ADMIN_ALLOWED_EMAILS", "").split(",") if value.strip())
    if not emails or any(len(email) > 254 or not _EMAIL.fullmatch(email) for email in emails):
        raise RuntimeError("请在 INVENTORY_ADMIN_ALLOWED_EMAILS 配置获准访问后台的邮箱白名单")
    return host, issuer, audience, emails


def validate_remote_admin_config() -> None:
    if remote_admin_enabled():
        _remote_config()


@lru_cache(maxsize=4)
def _jwks_client(issuer: str) -> PyJWKClient:
    # URL 仅由经过限制的官方 team 域名生成，不读取 JWT 中的 jku/x5u/iss 作为网络地址。
    return PyJWKClient(f"{issuer}/cdn-cgi/access/certs", cache_jwk_set=True,
                       lifespan=300, cache_keys=False, timeout=5)


def verify_remote_admin_request(request: Request) -> dict:
    try:
        if not remote_admin_enabled():
            raise HTTPException(status_code=403, detail="公网后台尚未启用")
        config = _remote_config()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="公网后台访问配置不完整，请联系管理员") from None
    host, issuer, audience, emails = config
    try:
        correct_host = (len(request.headers.getlist("host")) == 1 and request.url.hostname == host
                        and request.url.port in (None, 443))
    except ValueError:
        correct_host = False
    if not correct_host:
        raise HTTPException(status_code=403, detail="请通过指定后台入口访问")
    assertions = request.headers.getlist("cf-access-jwt-assertion")
    if len(assertions) != 1 or not assertions[0] or len(assertions[0]) > MAX_ASSERTION_LENGTH:
        raise HTTPException(status_code=403, detail="缺少有效的后台身份验证")
    token = assertions[0]
    cache_key = (config, hashlib.sha256(token.encode()).hexdigest())
    cached = getattr(request.state, "remote_admin_claims", None)
    if (getattr(request.state, "_remote_admin_validation", None) == cache_key
            and isinstance(cached, dict) and cached.get("exp", 0) > time.time()):
        return dict(cached)
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not 0 < len(header["kid"]) <= 256:
            raise jwt.InvalidTokenError()
        signing_key = _jwks_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], issuer=issuer, audience=audience,
                            options={"require": ["iss", "aud", "exp", "iat", "email", "type"]})
        email = claims.get("email")
        if (type(claims["exp"]) is not int or type(claims["iat"]) is not int
                or claims.get("type") != "app" or not isinstance(email, str) or email.strip().lower() not in emails):
            raise jwt.InvalidTokenError()
    except (PyJWKClientConnectionError, PyJWKSetError, OSError):
        raise HTTPException(status_code=503, detail="后台身份验证服务暂不可用，请稍后重试") from None
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(status_code=403, detail="后台身份验证无效或无权访问") from None
    request.state.remote_admin_claims = dict(claims)
    request.state._remote_admin_validation = cache_key
    return dict(claims)
