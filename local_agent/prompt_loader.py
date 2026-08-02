"""Prompt template loading for the local inventory agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import json


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


def _read_prompt(name: str) -> str:
    path = (PROMPT_DIR / name).resolve()
    if PROMPT_DIR.resolve() not in path.parents:
        raise ValueError("invalid prompt path")
    return path.read_text(encoding="utf-8").strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_inventory_analysis_prompt(
    payload: dict[str, Any],
    rule_items: list[dict[str, str]],
    tool_manifest: list[dict[str, Any]],
    context_meta: dict[str, Any],
) -> list[dict[str, str]]:
    system_prompt = _read_prompt("inventory_analysis.system.md")
    user_prompt = _read_prompt("inventory_analysis.user.md")
    replacements = {
        "{{tool_manifest_json}}": _json(tool_manifest),
        "{{context_meta_json}}": _json(context_meta),
        "{{rule_items_json}}": _json(rule_items),
        "{{payload_json}}": _json(payload),
    }
    for key, value in replacements.items():
        user_prompt = user_prompt.replace(key, value)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
