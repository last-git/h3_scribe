from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import AuthoringInput, CastPicturePayload, ComposerOutput, InitialPicturePayload
from .render import render_i2va, render_ref2va
from .requests import cast_request, compose_request, initial_request
from .semantics import (
    authoring_draft,
    canonicalize_reference_analysis,
    composer_input,
    picture_roles,
    validate_composer_output,
)
from .serialization import dump_json, parse_model_json

CATEGORY = "H3 Scribe"


def _first(values: list[Any] | Any, default: Any = None) -> Any:
    if isinstance(values, list):
        return values[0] if values else default
    return default if values is None else values


def _qwen_gguf_registry() -> str:
    """Return a Comfy model category that can actually see GGUF text encoders.

    Core ComfyUI's ``text_encoders`` category intentionally filters to the
    standard PyTorch/safetensors extensions, so ``get_filename_list`` there
    cannot see ``.gguf`` files even when they physically live under
    ``models/text_encoders``.  ComfyUI-GGUF exposes the same registered paths
    as ``clip_gguf``; use it when available.  Otherwise create a lightweight
    H3 alias over Comfy's registered text-encoder roots and filter to GGUF in
    this node.  This keeps extra_model_paths.yaml support without owning model
    loading or a separate model directory.
    """
    import folder_paths

    try:
        folder_paths.get_folder_paths("clip_gguf")
        return "clip_gguf"
    except (KeyError, ValueError):
        pass

    category = "h3_qwen_gguf"
    try:
        existing = set(folder_paths.get_folder_paths(category))
    except (KeyError, ValueError):
        existing = set()

    for root in folder_paths.get_folder_paths("text_encoders"):
        if root not in existing:
            folder_paths.add_model_folder_path(category, root)
    return category


def _text_encoder_ggufs() -> tuple[list[str], list[str]]:
    # Import lazily so the package remains importable in ordinary unit tests
    # without a ComfyUI installation on sys.path.
    import folder_paths

    category = _qwen_gguf_registry()
    files = [
        name
        for name in folder_paths.get_filename_list(category)
        if str(name).casefold().endswith(".gguf")
    ]
    if not files:
        # Comfy combo widgets need at least one value to render. The sentinel
        # gives a useful error at execution time instead of failing node load.
        return ["<no GGUF text encoders found>"], ["<no GGUF mmproj found>"]

    model_files = [name for name in files if "mmproj" not in Path(name).name.casefold()] or files
    mmproj_files = [name for name in files if "mmproj" in Path(name).name.casefold()] or files

    preferred_model = "Qwen3.6-27B-Uncensored-HauhauCS-Balanced-Q4_K_P.gguf"
    preferred_mmproj = "mmproj-Qwen3.6-27B-Uncensored-HauhauCS-Balanced-f16.gguf"

    def prefer(items: list[str], basename: str) -> list[str]:
        preferred = [name for name in items if Path(name).name == basename]
        rest = [name for name in items if Path(name).name != basename]
        return preferred + rest

    return prefer(model_files, preferred_model), prefer(mmproj_files, preferred_mmproj)


class H3QwenModelSelector:
    """Resolve ComfyUI text-encoder dropdown selections for Simple Qwen Base Config."""

    @classmethod
    def INPUT_TYPES(cls):
        models, mmprojs = _text_encoder_ggufs()
        return {
            "required": {
                "model": (models,),
                "mmproj": (mmprojs,),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("config_override",)
    FUNCTION = "select"
    CATEGORY = f"{CATEGORY}/Qwen"
    DESCRIPTION = (
        "Select the Qwen GGUF and multimodal projector from ComfyUI's registered text_encoders paths. "
        "Only model_path/mmproj_path are overridden; Simple Qwen still owns model loading and runtime."
    )

    def select(self, model: str, mmproj: str):
        import folder_paths

        if model.startswith("<no ") or mmproj.startswith("<no "):
            raise ValueError(
                "No Qwen GGUF model/projector was found in ComfyUI's text_encoders paths"
            )
        category = _qwen_gguf_registry()
        model_path = folder_paths.get_full_path_or_raise(category, model)
        mmproj_path = folder_paths.get_full_path_or_raise(category, mmproj)
        return (dump_json({"model_path": model_path, "mmproj_path": mmproj_path}),)


class H3InitialQwenRequest:
    @classmethod
    def INPUT_TYPES(cls):
        return {"optional": {"base_config": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("system_prompt", "user_prompt", "config_override", "seed")
    FUNCTION = "build"
    CATEGORY = f"{CATEGORY}/Qwen"

    def build(self, base_config=None):
        return initial_request(base_config)


class H3CastQwenRequest:
    @classmethod
    def INPUT_TYPES(cls):
        return {"optional": {"base_config": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("system_prompt", "user_prompt", "config_override", "seed")
    FUNCTION = "build"
    CATEGORY = f"{CATEGORY}/Qwen"

    def build(self, base_config=None):
        return cast_request(base_config)


class H3CanonicalizeReferences:
    """Collect Initial + zero-or-more independently analyzed Cast results."""

    INPUT_IS_LIST = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "mode": (["i2va", "ref2va"], {"default": "ref2va"}),
                "initial_picture_number": (
                    "INT",
                    {
                        "default": 1,
                        "min": 0,
                        "max": 9,
                        "tooltip": "0 = no Initial picture (Ref2VA only). Cast results are in Picture order excluding Initial.",
                    },
                ),
            },
            "optional": {
                "initial_json": ("STRING", {"forceInput": True}),
                "cast_json": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("authoring_draft_json",)
    FUNCTION = "canonicalize"
    CATEGORY = f"{CATEGORY}/Semantics"

    def canonicalize(self, mode, initial_picture_number, initial_json=None, cast_json=None):
        mode_value = str(_first(mode, "ref2va"))
        initial_number = int(_first(initial_picture_number, 0) or 0)
        initial_values = [x for x in (initial_json or []) if isinstance(x, str) and x.strip()]
        cast_values = [x for x in (cast_json or []) if isinstance(x, str) and x.strip()]

        if mode_value == "i2va":
            if len(initial_values) != 1:
                raise ValueError("I2VA requires exactly one Initial Qwen result")
            if cast_values:
                raise ValueError("I2VA does not accept Cast Qwen results")
            initial_number = 1
            reference_count = 1
        else:
            if len(initial_values) > 1:
                raise ValueError("Ref2VA supports at most one Initial Qwen result")
            if initial_values:
                reference_count = len(cast_values) + 1
                if not 1 <= initial_number <= reference_count:
                    raise ValueError(
                        f"initial_picture_number must be 1..{reference_count} when Initial is connected"
                    )
            else:
                initial_number = 0
                reference_count = len(cast_values)
                if reference_count < 1:
                    raise ValueError("Ref2VA requires at least one Initial or Cast result")

        roles = picture_roles(mode_value, reference_count, None if initial_number == 0 else initial_number)
        cast_iter = iter(cast_values)
        payloads = []
        for role in roles:
            if role == "initial":
                payloads.append(parse_model_json(initial_values[0], InitialPicturePayload))
            else:
                try:
                    raw = next(cast_iter)
                except StopIteration as exc:
                    raise ValueError("not enough Cast Qwen results for reference layout") from exc
                payloads.append(parse_model_json(raw, CastPicturePayload))

        analysis = canonicalize_reference_analysis(mode=mode_value, roles=roles, payloads=payloads)
        return (dump_json(authoring_draft(analysis)),)


class H3ComposeQwenRequest:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"authoring_json": ("STRING", {"forceInput": True})},
            "optional": {"base_config": ("STRING", {"forceInput": True})},
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("system_prompt", "user_prompt", "config_override", "seed")
    FUNCTION = "build"
    CATEGORY = f"{CATEGORY}/Qwen"

    def build(self, authoring_json: str, base_config=None):
        authoring = parse_model_json(authoring_json, AuthoringInput)
        system, user, config, seed = compose_request(authoring, base_config)
        return system, user, config, seed


class H3ValidateAndRender:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "authoring_json": ("STRING", {"forceInput": True}),
                "composer_json": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("final_prompt",)
    FUNCTION = "render"
    CATEGORY = f"{CATEGORY}/Semantics"

    def render(self, authoring_json: str, composer_json: str):
        authoring = parse_model_json(authoring_json, AuthoringInput)
        inputs = composer_input(authoring)
        output = parse_model_json(composer_json, ComposerOutput)
        validate_composer_output(inputs, output)
        prompt = render_ref2va(output, inputs) if authoring.mode == "ref2va" else render_i2va(output, inputs)
        return (prompt,)


def _merge_analyzed_authoring(current: AuthoringInput, analyzed: AuthoringInput) -> AuthoringInput:
    """Refresh image-derived fields while preserving authoritative user instructions.

    Analyze owns the reference/provenance fields, Subject Appearance, Initial and Style.
    The user owns Throughout and the complete Shots array. Temporary dangling Subject
    references are allowed here so the user can repair preserved instructions before Compose.
    """
    merged = analyzed.model_dump(mode="python")
    merged["throughout"] = current.throughout
    merged["shots"] = [shot.model_dump(mode="python") for shot in current.shots]
    return AuthoringInput.model_validate(merged)


class H3AuthoringEditor:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "authoring_json": (
                    "STRING",
                    {"default": "", "multiline": True, "dynamicPrompts": False},
                ),
                "source_snapshot": (
                    "STRING",
                    {"default": "", "multiline": True, "dynamicPrompts": False},
                ),
            },
            "optional": {
                "source": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("authoring_json",)
    FUNCTION = "edit"
    CATEGORY = f"{CATEGORY}/UI"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Persistent H3 authoring form and phase-1 Partial Execution target. In the bundled workflows Analyze is a normal upstream "
        "dependency: first run imports the draft, changed Analyze results refresh image-derived "
        "fields, and unchanged sources preserve all user edits. Comfy caching skips Analyze when "
        "only downstream authoring/final-generation inputs change."
    )

    def edit(self, authoring_json: str, source_snapshot: str, source: str | None = None):
        source = source if isinstance(source, str) else ""
        current_text = authoring_json or ""
        snapshot = source_snapshot or ""

        if source.strip():
            if current_text.strip() and source == snapshot:
                # The image-derived source is unchanged, so the current editor is
                # authoritative. Avoid even reparsing the upstream draft here.
                selected = parse_model_json(current_text, AuthoringInput)
            else:
                analyzed = parse_model_json(source, AuthoringInput)
                if not current_text.strip():
                    selected = analyzed
                else:
                    current = parse_model_json(current_text, AuthoringInput)
                    selected = _merge_analyzed_authoring(current, analyzed)
            selected_text = dump_json(selected)
            selected_snapshot = source
        else:
            if not current_text.strip():
                raise ValueError("H3 Authoring Editor is empty. Run ① ANALYZE first.")
            selected = parse_model_json(current_text, AuthoringInput)
            selected_text = dump_json(selected)
            selected_snapshot = snapshot

        return {
            "ui": {
                "h3_editor_value": [selected_text],
                "h3_editor_source": [selected_snapshot],
            },
            "result": (selected_text,),
        }


class H3TextEditor:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": (
                    "STRING",
                    {"default": "", "multiline": True, "dynamicPrompts": False},
                ),
                "source_snapshot": (
                    "STRING",
                    {"default": "", "multiline": True, "dynamicPrompts": False},
                ),
            },
            "optional": {
                "source": ("STRING", {"forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("text",)
    FUNCTION = "edit"
    CATEGORY = f"{CATEGORY}/UI"
    OUTPUT_NODE = True
    DESCRIPTION = (
        "Persistent editable final H3 prompt and phase-2 Partial Execution target. In the bundled workflows Compose is a normal "
        "upstream dependency. A changed Compose result refreshes the editor; otherwise the "
        "current user-edited text is returned verbatim, so changing only this editor invalidates "
        "only the downstream generation path while upstream Qwen results remain cached."
    )

    def edit(self, text: str, source_snapshot: str, source: str | None = None):
        # Deliberately do not strip, normalize, render, or re-compose final text.
        source = source if isinstance(source, str) else ""
        current = text if isinstance(text, str) else str(text or "")
        snapshot = source_snapshot if isinstance(source_snapshot, str) else ""

        if source and (not current or source != snapshot):
            selected = source
            selected_snapshot = source
        else:
            selected = current
            selected_snapshot = snapshot

        return {
            "ui": {
                "h3_editor_value": [selected],
                "h3_editor_source": [selected_snapshot],
            },
            "result": (selected,),
        }



NODE_CLASS_MAPPINGS = {
    "H3Scribe_QwenModelSelector": H3QwenModelSelector,
    "H3Scribe_InitialQwenRequest": H3InitialQwenRequest,
    "H3Scribe_CastQwenRequest": H3CastQwenRequest,
    "H3Scribe_CanonicalizeReferences": H3CanonicalizeReferences,
    "H3Scribe_ComposeQwenRequest": H3ComposeQwenRequest,
    "H3Scribe_ValidateAndRender": H3ValidateAndRender,
    "H3Scribe_AuthoringEditor": H3AuthoringEditor,
    "H3Scribe_TextEditor": H3TextEditor,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3Scribe_QwenModelSelector": "H3 Qwen Model Selector",
    "H3Scribe_InitialQwenRequest": "H3 Initial → Qwen",
    "H3Scribe_CastQwenRequest": "H3 Cast → Qwen",
    "H3Scribe_CanonicalizeReferences": "H3 Canonicalize References",
    "H3Scribe_ComposeQwenRequest": "H3 Compose → Qwen",
    "H3Scribe_ValidateAndRender": "H3 Validate & Render",
    "H3Scribe_AuthoringEditor": "H3 Authoring Editor",
    "H3Scribe_TextEditor": "H3 Final Prompt Editor",
}
