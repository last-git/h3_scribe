import json

from h3_scribe.models import AuthoringInput, AuthoringSubject, UserShot
from h3_scribe.requests import cast_request, compose_request, initial_request


def test_initial_request_reproduces_old_json_mode_and_sampling():
    system, user, config_text, seed = initial_request(
        {"model_path": "qwen.gguf", "mmproj_path": "mmproj.gguf", "repeat_penalty": 1.1}
    )
    config = json.loads(config_text)
    assert "SHARED APPEARANCE CONTRACT" in system
    assert "Return exactly one JSON object matching this schema" in user
    assert config["extra_completion_response_format"] == {"type": "json_object"}
    assert config["temperature"] == 0.0
    assert config["top_p"] == 1.0
    assert config["top_k"] == 0
    assert config["min_p"] == 0.0
    assert config["repeat_penalty"] == 1.0
    assert config["enable_thinking"] is False
    assert seed == 0


def test_cast_request_is_image_stage():
    _, _, config_text, _ = cast_request({"model_path": "qwen.gguf"})
    config = json.loads(config_text)
    assert config["max_images"] == 1
    assert "chat_format_from_gguf" not in config


def test_compose_request_is_text_only_and_uses_raw_unicode():
    authoring = AuthoringInput(
        mode="ref2va",
        reference_image_count=1,
        initial_picture_number=None,
        subjects=[
            AuthoringSubject(
                label="<Subject 1>",
                picture_number=1,
                source_role="cast",
                appearance_ja="長い銀髪、青い目。",
            )
        ],
        shots=[UserShot(motion="<Subject 1>が右手を上げる。")],
    )
    _, user, config_text, seed = compose_request(authoring, {"model_path": "qwen.gguf"})
    config = json.loads(config_text)
    assert "長い銀髪" in user
    assert "\\u9577" not in user
    assert "chat_format_from_gguf" not in config
    assert config["force_mmproj"] is True
    assert config["max_tokens"] == 3072
    assert seed == 0


def test_compose_request_hides_shot_start_timestamps_from_qwen():
    authoring = AuthoringInput(
        mode="ref2va",
        reference_image_count=1,
        initial_picture_number=None,
        subjects=[
            AuthoringSubject(
                label="<Subject 1>",
                picture_number=1,
                source_role="cast",
                appearance_ja="長い銀髪、青い目。",
            )
        ],
        shots=[
            UserShot(motion="<Subject 1>が右手を上げる。"),
            UserShot(start_time_seconds=3.5, motion="<Subject 1>が左を向く。"),
        ],
    )

    _, user, _, _ = compose_request(authoring)
    semantic_text, _ = user.split("\n\n", 1)
    semantic_payload = json.loads(semantic_text)

    assert [shot["motion"] for shot in semantic_payload["shots"]] == [
        "<Subject 1>が右手を上げる。",
        "<Subject 1>が左を向く。",
    ]
    assert all("start_time_seconds" not in shot for shot in semantic_payload["shots"])
