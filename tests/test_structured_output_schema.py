import json

from h3_scribe.models import (
    AuthoringInput,
    AuthoringSubject,
    CastPicturePayload,
    ComposerOutput,
    InitialPicturePayload,
    UserShot,
)
from h3_scribe.requests import cast_request, compose_request, initial_request

_SCHEMA_MARKER = "Do not use Markdown/code fences or surrounding prose: "


def _response_format(config_text: str) -> dict:
    return json.loads(config_text)["extra_completion_response_format"]


def _prompt_schema(user_prompt: str) -> dict:
    return json.loads(user_prompt.split(_SCHEMA_MARKER, 1)[1])


def _assert_schema_contract(user_prompt: str, config_text: str, model) -> dict:
    response_format = _response_format(config_text)
    schema = response_format["schema"]

    assert response_format["type"] == "json_object"
    assert schema == _prompt_schema(user_prompt)
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(schema["properties"])
    assert "maxLength" not in json.dumps(model.model_json_schema())
    assert "maxLength" not in json.dumps(schema)
    return schema


def test_initial_request_uses_one_strict_schema_for_prompt_and_grammar():
    _, user, config_text, _ = initial_request()
    schema = _assert_schema_contract(user, config_text, InitialPicturePayload)

    assert schema["required"] == ["subjects", "initial_ja", "style_ja"]


def test_cast_request_uses_one_strict_schema_for_prompt_and_grammar():
    _, user, config_text, _ = cast_request()
    schema = _assert_schema_contract(user, config_text, CastPicturePayload)

    assert schema["required"] == ["appearance_ja"]


def test_ref2va_compose_schema_fixes_shot_subject_style_and_summary_structure():
    authoring = AuthoringInput(
        mode="ref2va",
        reference_image_count=1,
        initial_picture_number=1,
        subjects=[
            AuthoringSubject(
                label="<Subject 1>",
                picture_number=1,
                source_role="initial",
                appearance_ja="Long silver hair.",
            ),
            AuthoringSubject(
                label="<Subject 2>",
                picture_number=1,
                source_role="initial",
                appearance_ja="Short black hair.",
            ),
        ],
        style_ja="Anime style.",
        shots=[UserShot(), UserShot(start_time_seconds=3.0)],
    )
    _, user, config_text, _ = compose_request(authoring)
    schema = _assert_schema_contract(user, config_text, ComposerOutput)
    properties = schema["properties"]

    assert properties["shots"]["minItems"] == 2
    assert properties["shots"]["maxItems"] == 2
    assert properties["subject_appearances"]["minItems"] == 2
    assert properties["subject_appearances"]["maxItems"] == 2
    assert properties["style_description"]["minLength"] == 1
    assert properties["summary_overview"]["minLength"] == 1
    assert schema["$defs"]["ComposerSubjectAppearance"]["properties"]["label"]["enum"] == [
        "<Subject 1>",
        "<Subject 2>",
    ]


def test_i2va_compose_schema_requires_empty_ref2va_only_fields():
    authoring = AuthoringInput(
        mode="i2va",
        reference_image_count=1,
        initial_picture_number=1,
        subjects=[
            AuthoringSubject(
                label="<Subject 1>",
                picture_number=1,
                source_role="initial",
                appearance_ja="Long silver hair.",
            )
        ],
        style_ja="",
        shots=[UserShot()],
    )
    _, user, config_text, _ = compose_request(authoring)
    schema = _assert_schema_contract(user, config_text, ComposerOutput)
    properties = schema["properties"]

    assert properties["shots"]["minItems"] == 1
    assert properties["shots"]["maxItems"] == 1
    assert properties["subject_appearances"]["minItems"] == 0
    assert properties["subject_appearances"]["maxItems"] == 0
    assert properties["style_description"]["const"] == ""
    assert properties["summary_overview"]["const"] == ""
