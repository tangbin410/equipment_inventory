"""Standalone local agent for inventory AI analysis."""

from __future__ import annotations

import json
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request

from .config import deepseek_config, load_local_env
from .llm_client import http_error_detail, post_deepseek_chat
from .rules import ANALYSIS_KEYS, run_rules


load_local_env()
app = FastAPI(title="Equipment Inventory Local Agent")


def _parse_json_array(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        data = json.loads(text[start:end + 1]) if start >= 0 and end > start else []
    if isinstance(data, dict):
        for key in ("analysis_items", "items", "result", "问题清单"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        return []
    return [
        {key: str(item.get(key) or "") for key in ANALYSIS_KEYS}
        for item in data
        if isinstance(item, dict)
    ]


def _merge_items(rule_items: list[dict[str, str]], model_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in [*rule_items, *model_items]:
        normalized = {key: str(item.get(key) or "") for key in ANALYSIS_KEYS}
        if not normalized["问题类型"]:
            continue
        key = (normalized["问题类型"], normalized["涉及机房"], normalized["涉及设备"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged[:30]


def _analysis_prompt(payload: dict[str, Any], rule_items: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是运营商传输网管资产盘点本地 agent。"
                "你会收到已经脱敏的盘点数据和本地规则初筛结果。"
                "只基于输入内容分析，不编造机房、设备或条码。"
                "输出必须是 JSON 数组。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请结合本地规则结果，补充识别盘点数据质量问题，并输出清单式整改建议。"
                "数组元素字段固定为：问题类型、涉及机房、涉及设备、风险等级、判断依据、整改建议、优先级。"
                "风险等级只能是高/中/低，优先级只能是P1/P2/P3。"
                f"\n\n本地规则初筛：{json.dumps(rule_items, ensure_ascii=False)}"
                f"\n\n脱敏盘点数据：{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    rule_items = run_rules(rows)
    try:
        content = post_deepseek_chat(_analysis_prompt(payload, rule_items), temperature=0.2)
        model_items = _parse_json_array(content)
    except Exception:
        model_items = []
    return {
        "analysis_items": _merge_items(rule_items, model_items),
        "rule_items": rule_items,
        "agent": "local-inventory-agent",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    api_key, model, base_url = deepseek_config()
    return {
        "ok": True,
        "agent": "local-inventory-agent",
        "model_provider": "deepseek",
        "model_name": model,
        "base_url": base_url,
        "api_key_configured": bool(api_key),
    }


@app.post("/analyze")
async def agent_entry(request: Request) -> dict[str, Any]:
    payload = await request.json()
    request_type = payload.get("type") if isinstance(payload, dict) else None
    try:
        if request_type == "health_check":
            content = post_deepseek_chat(
                [
                    {"role": "system", "content": "你是连通性检测助手。"},
                    {"role": "user", "content": "请只回复 OK"},
                ],
                temperature=0,
            )
            return {"ok": True, "message": f"local agent ok, model replied: {content.strip()[:40]}"}
        return analyze(payload)
    except requests.HTTPError as exc:
        raise HTTPException(status_code=502, detail=http_error_detail(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
