from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import (
    AuthoringInput,
    CastPicturePayload,
    ComposerInput,
    ComposerOutput,
    InitialPicturePayload,
)
from .semantics import composer_input
from .serialization import dump_json

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _read_prompt(name: str) -> str:
    text = (PROMPT_DIR / name).read_text(encoding="utf-8")
    if name in {"builder_extract_initial.md", "builder_extract_cast.md"}:
        rules = (PROMPT_DIR / "builder_appearance_rules.md").read_text(encoding="utf-8")
        text += "\n\nSHARED APPEARANCE CONTRACT\n\n" + rules
    return text


def _schema_instruction(schema: dict[str, Any]) -> str:
    serialized = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return (
        "Return exactly one JSON object matching this schema. "
        "Do not use Markdown/code fences or surrounding prose: "
        + serialized
    )


def _parse_config(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("base_config must be a JSON object")
        return parsed
    raise TypeError(f"unsupported base_config type: {type(value).__name__}")


def _response_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema["required"] = list(schema["properties"])
    return schema


def _composer_response_schema(inputs: ComposerInput) -> dict[str, Any]:
    schema = _response_schema(ComposerOutput)
    properties = schema["properties"]

    shot_count = len(inputs.shots)
    properties["shots"]["minItems"] = shot_count
    properties["shots"]["maxItems"] = shot_count

    appearance_count = len(inputs.subjects) if inputs.mode == "ref2va" else 0
    properties["subject_appearances"]["minItems"] = appearance_count
    properties["subject_appearances"]["maxItems"] = appearance_count

    if inputs.mode == "ref2va":
        properties["summary_overview"]["minLength"] = 1
        labels = [subject.label for subject in inputs.subjects]
        if labels:
            schema["$defs"]["ComposerSubjectAppearance"]["properties"]["label"]["enum"] = labels
    else:
        properties["summary_overview"]["const"] = ""

    if inputs.style_ja:
        properties["style_description"]["minLength"] = 1
    else:
        properties["style_description"]["const"] = ""

    return schema


def _stage_config(
    base_config: Any, *, max_tokens: int, text_only: bool, response_schema: dict[str, Any]
) -> str:
    config = _parse_config(base_config)
    # H3 owns these deterministic extraction/composition settings; Simple Qwen owns execution.
    config.update(
        {
            "script": "qwen3vl_run.py",
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "top_p": 1.0,
            "top_k": 0,
            "min_p": 0.0,
            "repeat_penalty": 1.0,
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
            "enable_thinking": False,
            "force_mmproj": text_only,
            "extra_completion_response_format": {
                "type": "json_object",
                "schema": response_schema,
            },
        }
    )
    # Simple Qwen's Qwen35ChatHandler owns the Qwen3.6 thinking switch. With no
    # image, force_mmproj keeps that same handler active for Composer instead of
    # falling back to a generic text-only GGUF template that cannot reliably
    # receive enable_thinking=False. This also mirrors the old always-multimodal
    # llama-server runtime.
    config.pop("chat_format_from_gguf", None)
    if not text_only:
        config["max_images"] = 1
    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


def initial_request(base_config: Any = None) -> tuple[str, str, str, int]:
    schema = _response_schema(InitialPicturePayload)
    return (
        _read_prompt("builder_extract_initial.md"),
        _schema_instruction(schema),
        _stage_config(
            base_config,
            max_tokens=2048,
            text_only=False,
            response_schema=schema,
        ),
        0,
    )


def cast_request(base_config: Any = None) -> tuple[str, str, str, int]:
    schema = _response_schema(CastPicturePayload)
    return (
        _read_prompt("builder_extract_cast.md"),
        _schema_instruction(schema),
        _stage_config(
            base_config,
            max_tokens=2048,
            text_only=False,
            response_schema=schema,
        ),
        0,
    )


def compose_request(
    authoring: AuthoringInput, base_config: Any = None
) -> tuple[str, str, str, int]:
    inputs = composer_input(authoring)
    prompt_name = (
        "builder_compose_ref2va.md" if authoring.mode == "ref2va" else "builder_compose_i2va.md"
    )
    semantic_payload = inputs.model_dump(
        mode="json", exclude={"shots": {"__all__": {"start_time_seconds"}}}
    )
    semantic_json = dump_json(semantic_payload, pretty=False)
    schema = _composer_response_schema(inputs)
    user_prompt = semantic_json + "\n\n" + _schema_instruction(schema)
    return (
        _read_prompt(prompt_name),
        user_prompt,
        _stage_config(
            base_config,
            max_tokens=3072,
            text_only=True,
            response_schema=schema,
        ),
        0,
    )
