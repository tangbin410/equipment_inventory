"""Configuration helpers for the local inventory AI agent."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"


def load_local_env() -> None:
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), clean_value(value))


def clean_value(value: str) -> str:
    return str(value or "").strip().strip("\"'")


def has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in str(value or ""))


def deepseek_config() -> tuple[str, str, str]:
    load_local_env()
    api_key = clean_value(os.getenv("DEEPSEEK_API_KEY", ""))
    model = clean_value(os.getenv("DEEPSEEK_MODEL", ""))
    base_url = clean_value(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    if not api_key or len(api_key) < 10 or has_control_chars(api_key):
        raise RuntimeError("DEEPSEEK_API_KEY is missing or invalid")
    if not model or has_control_chars(model):
        raise RuntimeError("DEEPSEEK_MODEL is missing or invalid")
    if not base_url.lower().startswith(("http://", "https://")) or has_control_chars(base_url):
        raise RuntimeError("DEEPSEEK_BASE_URL is invalid")
    return api_key, model, base_url


def request_timeout() -> int:
    load_local_env()
    return int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))


def agent_token() -> str:
    load_local_env()
    token = clean_value(os.getenv("INVENTORY_AGENT_TOKEN", ""))
    if not token or len(token) < 24 or has_control_chars(token):
        raise RuntimeError("INVENTORY_AGENT_TOKEN is missing or too short")
    return token
