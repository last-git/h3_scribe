"""Structured-output parsing and JSON serialization helpers."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

def parse_model_json(text: str, model: type[BaseModel]) -> BaseModel:
    raw = text.strip()
    if raw.startswith("❌ Inference failed:"):
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        detail = next(
            (line for line in lines[1:] if not line.casefold().startswith("check console")),
            "Unknown Simple Qwen error",
        )
        raise ValueError(f"Simple Qwen inference failed: {detail}")
    if "<think>" in raw.casefold():
        raise ValueError("Qwen response leaked a thinking block")
    fenced = re.fullmatch(
        r"```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced is not None:
        raw = fenced.group("body").strip()
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Qwen returned non-JSON text; only a single outer JSON code fence is tolerated"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError("Qwen structured output must be exactly one JSON object")
    return model.model_validate(value)


def dump_json(value: BaseModel | dict[str, Any] | list[Any], *, pretty: bool = True) -> str:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
