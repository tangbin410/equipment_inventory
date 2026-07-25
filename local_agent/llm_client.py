"""DeepSeek client used by the local agent."""

from __future__ import annotations

import json
from typing import Any

import requests

from .config import deepseek_config, request_timeout


def post_deepseek_chat(messages: list[dict[str, str]], temperature: float = 0.2) -> str:
    api_key, model, base_url = deepseek_config()
    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": temperature},
        timeout=request_timeout(),
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def http_error_detail(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    status = f"HTTP {response.status_code}"
    if response.reason:
        status = f"{status} {response.reason}"
    text = response.text.strip()
    if not text:
        return f"{status}: empty response body"
    try:
        data: Any = response.json()
    except ValueError:
        return f"{status}: {text[:500]}"
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or json.dumps(error, ensure_ascii=False)
            return f"{status}: {message}"
        detail = data.get("detail") or data.get("message")
        if detail:
            return f"{status}: {detail}"
    return f"{status}: {text[:500]}"

