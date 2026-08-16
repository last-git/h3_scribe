import json

from h3_scribe.nodes import (
    H3AuthoringEditor,
    H3CanonicalizeReferences,
    H3ComposeQwenRequest,
    H3QwenModelSelector,
    H3TextEditor,
    H3ValidateAndRender,
)


def test_ref2va_list_canonicalize_reconstructs_picture_order():
    node = H3CanonicalizeReferences()
    result = node.canonicalize(
        mode=["ref2va"],
        initial_picture_number=[2],
        initial_json=[
            json.dumps(
                {
                    "subjects": [{"appearance_ja": "黒髪。"}],
                    "initial_ja": "subject_0は中央に立つ。",
                    "style_ja": "アニメ調。",
                },
                ensure_ascii=False,
            )
        ],
        cast_json=[
            json.dumps({"appearance_ja": "銀髪。"}, ensure_ascii=False),
            json.dumps({"appearance_ja": "茶髪。"}, ensure_ascii=False),
        ],
    )
    draft = json.loads(result[0])
    assert draft["reference_image_count"] == 3
    assert [(s["picture_number"], s["source_role"]) for s in draft["subjects"]] == [
        (1, "cast"),
        (2, "initial"),
        (3, "cast"),
    ]
    assert draft["initial_ja"].startswith("<Subject 2>")


def test_validate_and_render_uses_edited_authoring_verbatim_as_source_of_truth():
    authoring = {
        "mode": "ref2va",
        "reference_image_count": 1,
        "initial_picture_number": None,
        "subjects": [
            {
                "label": "<Subject 1>",
                "picture_number": 1,
                "source_role": "cast",
                "appearance_ja": "銀髪。",
            }
        ],
        "initial_ja": "",
        "style_ja": "",
        "throughout": "",
        "shots": [{"start_time_seconds": None, "motion": "手を上げる。", "camera": "Fixed camera"}],
    }
    composer = {
        "subject_appearances": [{"label": "<Subject 1>", "appearance_en": "silver hair"}],
        "style_description": "",
        "summary_overview": "<Subject 1> raises a hand.",
        "shots": [{"description": "<Subject 1> raises a hand while the camera remains fixed."}],
    }
    (prompt,) = H3ValidateAndRender().render(
        json.dumps(authoring, ensure_ascii=False), json.dumps(composer, ensure_ascii=False)
    )
    assert "fully_preserved" in prompt
    assert "Fixed camera" not in prompt  # Composer prose is rendered, not raw Japanese source text.


def test_editors_are_comfy_output_nodes():
    assert H3AuthoringEditor.OUTPUT_NODE is True
    assert H3TextEditor.OUTPUT_NODE is True
    assert H3AuthoringEditor.RETURN_TYPES == ("STRING",)
    assert H3TextEditor.RETURN_TYPES == ("STRING",)


def test_public_nodes_hide_debug_only_outputs():
    assert H3CanonicalizeReferences.RETURN_NAMES == ("authoring_draft_json",)
    assert H3ComposeQwenRequest.RETURN_NAMES == (
        "system_prompt", "user_prompt", "config_override", "seed"
    )
    assert H3ValidateAndRender.RETURN_NAMES == ("final_prompt",)


def _authoring_json(motion=""):
    return json.dumps(
        {
            "mode": "ref2va",
            "reference_image_count": 1,
            "initial_picture_number": None,
            "subjects": [
                {
                    "label": "<Subject 1>",
                    "picture_number": 1,
                    "source_role": "cast",
                    "appearance_ja": "銀髪の人物。",
                }
            ],
            "initial_ja": "<Subject 1>が室内に立っている。",
            "style_ja": "アニメ調。",
            "throughout": "",
            "shots": [
                {
                    "start_time_seconds": None,
                    "motion": motion,
                    "camera": "Fixed camera",
                }
            ],
        },
        ensure_ascii=False,
    )


def test_editors_accept_upstream_sources_and_return_persistent_widget_state():
    authoring_inputs = H3AuthoringEditor.INPUT_TYPES()
    text_inputs = H3TextEditor.INPUT_TYPES()
    assert "source" in authoring_inputs["optional"]
    assert authoring_inputs["optional"]["source"][1]["forceInput"] is True
    assert "source" in text_inputs["optional"]
    assert text_inputs["optional"]["source"][1]["forceInput"] is True

    edited = _authoring_json("右手を上げる。")
    result = H3AuthoringEditor().edit(edited, "old analyze source")
    normalized = result["result"][0]
    assert json.loads(normalized)["shots"][0]["motion"] == "右手を上げる。"
    assert result["ui"]["h3_editor_value"] == [normalized]

    exact = "  user edited final prompt\nwith spacing  "
    text_result = H3TextEditor().edit(exact, "old compose source")
    assert text_result["result"] == (exact,)
    assert text_result["ui"]["h3_editor_value"] == [exact]


def test_authoring_editor_first_source_bootstraps_empty_state():
    source = _authoring_json("draft motion")
    result = H3AuthoringEditor().edit("", "", source)
    assert json.loads(result["result"][0])["shots"][0]["motion"] == "draft motion"
    assert result["ui"]["h3_editor_source"] == [source]


def test_authoring_editor_changed_source_refreshes_image_fields_but_preserves_user_instructions():
    current = json.loads(_authoring_json("user motion"))
    current["initial_ja"] = "old initial"
    current["style_ja"] = "old style"
    current["throughout"] = "keep throughout"
    current["shots"].append({
        "start_time_seconds": 2.5,
        "motion": "second motion",
        "camera": "slow pan",
    })
    current_text = json.dumps(current, ensure_ascii=False)

    analyzed = json.loads(_authoring_json("analysis default motion"))
    analyzed["initial_ja"] = "new initial"
    analyzed["style_ja"] = "new style"
    analyzed["subjects"][0]["appearance_ja"] = "new appearance"
    source = json.dumps(analyzed, ensure_ascii=False)

    result = H3AuthoringEditor().edit(current_text, "previous source", source)
    merged = json.loads(result["result"][0])
    assert merged["initial_ja"] == "new initial"
    assert merged["style_ja"] == "new style"
    assert merged["subjects"][0]["appearance_ja"] == "new appearance"
    assert merged["throughout"] == "keep throughout"
    assert merged["shots"] == current["shots"]
    assert result["ui"]["h3_editor_source"] == [source]


def test_authoring_editor_changed_subject_set_preserves_user_text_for_manual_repair():
    current = json.loads(_authoring_json("<Subject 1> waves to <Subject 2>"))
    current["reference_image_count"] = 2
    current["subjects"] = [
        {
            "label": "<Subject 1>",
            "picture_number": 1,
            "source_role": "cast",
            "appearance_ja": "銀髪の人物。",
        },
        {
            "label": "<Subject 2>",
            "picture_number": 2,
            "source_role": "cast",
            "appearance_ja": "黒髪の人物。",
        },
    ]
    current_text = json.dumps(current, ensure_ascii=False)

    analyzed = json.loads(_authoring_json(""))
    source = json.dumps(analyzed, ensure_ascii=False)

    result = H3AuthoringEditor().edit(current_text, "previous source", source)
    merged = json.loads(result["result"][0])
    assert [subject["label"] for subject in merged["subjects"]] == ["<Subject 1>"]
    assert merged["shots"][0]["motion"] == "<Subject 1> waves to <Subject 2>"

    repaired = dict(merged)
    repaired["shots"] = [dict(merged["shots"][0], motion="<Subject 1> waves") ]
    system, user, config, seed = H3ComposeQwenRequest().build(
        json.dumps(repaired, ensure_ascii=False)
    )
    assert system and user and isinstance(config, str) and isinstance(seed, int)


def test_authoring_editor_same_source_preserves_all_current_edits():
    current = json.loads(_authoring_json("user motion"))
    current["initial_ja"] = "user edited initial"
    current["style_ja"] = "user edited style"
    current["subjects"][0]["appearance_ja"] = "user edited appearance"
    current_text = json.dumps(current, ensure_ascii=False)
    result = H3AuthoringEditor().edit(current_text, "same source", "same source")
    preserved = json.loads(result["result"][0])
    assert preserved["initial_ja"] == "user edited initial"
    assert preserved["style_ja"] == "user edited style"
    assert preserved["subjects"][0]["appearance_ja"] == "user edited appearance"
    assert preserved["shots"][0]["motion"] == "user motion"


def test_text_editor_changed_compose_source_overwrites_draft_but_same_source_preserves_manual_edit():
    changed = H3TextEditor().edit("manual old", "old compose", "new compose")
    assert changed["result"] == ("new compose",)
    assert changed["ui"]["h3_editor_source"] == ["new compose"]

    same = H3TextEditor().edit("manual final edit", "compose source", "compose source")
    assert same["result"] == ("manual final edit",)
    assert same["ui"]["h3_editor_source"] == ["compose source"]


def test_authoring_editor_rejects_empty_state_before_analyze():
    import pytest

    with pytest.raises(ValueError, match="Run ① ANALYZE first"):
        H3AuthoringEditor().edit("", "")


def test_qwen_model_selector_uses_comfy_gguf_registry(monkeypatch):
    import sys
    import types

    preferred_model = "Qwen3.6-27B-Uncensored-HauhauCS-Balanced-Q3_K_P.gguf"
    preferred_mmproj = "mmproj-Qwen3.6-27B-Uncensored-HauhauCS-Balanced-f16.gguf"
    files = [
        "other.gguf",
        f"models/{preferred_mmproj}",
        preferred_model,
        "ignore.safetensors",
    ]

    def get_folder_paths(category):
        if category == "clip_gguf":
            return ["/comfy/models/text_encoders"]
        if category == "text_encoders":
            return ["/comfy/models/text_encoders", "/comfy/models/clip"]
        raise KeyError(category)

    fake = types.SimpleNamespace(
        get_folder_paths=get_folder_paths,
        add_model_folder_path=lambda category, root: None,
        get_filename_list=lambda category: files if category == "clip_gguf" else [],
        get_full_path_or_raise=lambda category, name: f"/comfy/{category}/{name}",
    )
    monkeypatch.setitem(sys.modules, "folder_paths", fake)

    inputs = H3QwenModelSelector.INPUT_TYPES()["required"]
    assert inputs["model"][0][0] == preferred_model
    assert inputs["mmproj"][0][0].endswith(preferred_mmproj)

    (override_json,) = H3QwenModelSelector().select(preferred_model, f"models/{preferred_mmproj}")
    override = json.loads(override_json)
    assert override == {
        "model_path": f"/comfy/clip_gguf/{preferred_model}",
        "mmproj_path": f"/comfy/clip_gguf/models/{preferred_mmproj}",
    }


def test_qwen_model_selector_falls_back_to_registered_text_encoder_roots(monkeypatch):
    import sys
    import types

    preferred_model = "Qwen3.6-27B-Uncensored-HauhauCS-Balanced-Q3_K_P.gguf"
    preferred_mmproj = "mmproj-Qwen3.6-27B-Uncensored-HauhauCS-Balanced-f16.gguf"
    categories = {
        "text_encoders": ["D:/ComfyUI/models/text_encoders", "E:/shared/text_encoders"]
    }

    def get_folder_paths(category):
        if category not in categories:
            raise KeyError(category)
        return list(categories[category])

    def add_model_folder_path(category, root):
        categories.setdefault(category, []).append(root)

    fake = types.SimpleNamespace(
        get_folder_paths=get_folder_paths,
        add_model_folder_path=add_model_folder_path,
        get_filename_list=lambda category: [preferred_model, preferred_mmproj, "ignore.safetensors"]
        if category == "h3_qwen_gguf"
        else [],
        get_full_path_or_raise=lambda category, name: f"D:/resolved/{name}",
    )
    monkeypatch.setitem(sys.modules, "folder_paths", fake)

    inputs = H3QwenModelSelector.INPUT_TYPES()["required"]
    assert inputs["model"][0][0] == preferred_model
    assert inputs["mmproj"][0][0] == preferred_mmproj
    assert categories["h3_qwen_gguf"] == categories["text_encoders"]

    (override_json,) = H3QwenModelSelector().select(preferred_model, preferred_mmproj)
    assert json.loads(override_json) == {
        "model_path": f"D:/resolved/{preferred_model}",
        "mmproj_path": f"D:/resolved/{preferred_mmproj}",
    }

def test_ref2va_initial_only_accepts_zero_cast_results():
    node = H3CanonicalizeReferences()
    (draft_json,) = node.canonicalize(
        mode=["ref2va"],
        initial_picture_number=[1],
        initial_json=[
            json.dumps(
                {
                    "subjects": [{"appearance_ja": "茶髪の人物。"}],
                    "initial_ja": "subject_0が室内に立っている。",
                    "style_ja": "アニメ調。",
                },
                ensure_ascii=False,
            )
        ],
        cast_json=[],
    )
    draft = json.loads(draft_json)
    assert draft["reference_image_count"] == 1
    assert [(s["picture_number"], s["source_role"]) for s in draft["subjects"]] == [(1, "initial")]


def test_parse_model_json_surfaces_simple_qwen_failure_before_json_error():
    import pytest
    from h3_scribe.models import CastPicturePayload
    from h3_scribe.serialization import parse_model_json

    with pytest.raises(ValueError, match=r"Simple Qwen inference failed: Inference timed out \(5 min\)\."):
        parse_model_json(
            "❌ Inference failed:\nInference timed out (5 min).\nCheck console for details.",
            CastPicturePayload,
        )
