import pytest
from pydantic import ValidationError

from h3_scribe.models import (
    AuthoringInput,
    AuthoringSubject,
    CastPicturePayload,
    ComposerOutput,
    ComposerShotOutput,
    ComposerSubjectAppearance,
    InitialPicturePayload,
    UserShot,
)
from h3_scribe.render import render_i2va, render_ref2va
from h3_scribe.semantics import (
    authoring_draft,
    canonicalize_reference_analysis,
    composer_input,
    picture_roles,
    validate_composer_output,
)
from h3_scribe.serialization import parse_model_json


def initial_payload() -> InitialPicturePayload:
    return InitialPicturePayload.model_validate(
        {
            "subjects": [
                {"appearance_ja": "長い銀髪、青い目、赤い星型のヘアピン。"},
                {"appearance_ja": "短い黒髪、丸眼鏡、緑のカーディガン。"},
            ],
            "initial_ja": "subject_0は左側に立ち、subject_1は右側に立っている。背景は濡れた屋上。",
            "style_ja": "セルアニメ調の2Dイラスト。",
        }
    )


def test_initial_payload_rejects_unknown_subject_reference():
    with pytest.raises(ValidationError, match="unknown local subjects"):
        InitialPicturePayload.model_validate(
            {
                "subjects": [{"appearance_ja": "銀髪。"}],
                "initial_ja": "subject_1は立っている。",
                "style_ja": "",
            }
        )


def test_picture_roles_are_code_owned():
    assert picture_roles("i2va", 1, None) == ["initial"]
    assert picture_roles("ref2va", 3, 2) == ["cast", "initial", "cast"]
    assert picture_roles("ref2va", 2, None) == ["cast", "cast"]


def test_canonicalization_preserves_subject_and_picture_semantics():
    analysis = canonicalize_reference_analysis(
        mode="ref2va",
        roles=["initial", "cast"],
        payloads=[initial_payload(), CastPicturePayload(appearance_ja="茶色の長い髪、白いブラウス。")],
    )
    assert [(s.label, s.picture_number, s.source_role) for s in analysis.subjects] == [
        ("<Subject 1>", 1, "initial"),
        ("<Subject 2>", 1, "initial"),
        ("<Subject 3>", 2, "cast"),
    ]
    assert analysis.initial_ja.startswith("<Subject 1>は左側に立ち、<Subject 2>は右側")
    draft = authoring_draft(analysis)
    assert draft.shots[0].camera == "Fixed camera"
    assert draft.throughout == ""


def authoring(mode="ref2va") -> AuthoringInput:
    return AuthoringInput(
        mode=mode,
        reference_image_count=1,
        initial_picture_number=1,
        subjects=[
            AuthoringSubject(
                label="<Subject 1>",
                picture_number=1,
                source_role="initial",
                appearance_ja="長い銀髪、青い目、赤い星型のヘアピン。",
            )
        ],
        initial_ja="<Subject 1>は両手を体の横に下ろして立っている。",
        style_ja="アニメ調。",
        throughout="顔立ちを維持する。",
        shots=[UserShot(motion="<Subject 1>が眼鏡を外す。", camera="Fixed camera")],
    )


def test_composer_validation_and_ref2va_renderer():
    inputs = composer_input(authoring())
    prose = ComposerOutput(
        subject_appearances=[
            ComposerSubjectAppearance(
                label="<Subject 1>",
                appearance_en="long silver hair, blue eyes, and a red star-shaped hairpin",
            )
        ],
        style_description="The target video uses a 2D anime illustration style.",
        summary_overview="<Subject 1> lowers both hands before beginning the requested action.",
        shots=[
            ComposerShotOutput(
                description="<Subject 1>, with long silver hair, stands with both hands lowered and then removes their glasses. The camera remains fixed."
            )
        ],
    )
    validate_composer_output(inputs, prose)
    prompt = render_ref2va(prose, inputs)
    assert "weak_reference" in prompt
    assert "<Subject 1>: fully_preserved" in prompt
    assert prompt.endswith("non_diegetic_music:\nN/A")


def test_i2va_alias_leak_is_rejected():
    inputs = composer_input(authoring("i2va"))
    prose = ComposerOutput(
        subject_appearances=[],
        style_description="The video uses a 2D anime illustration style.",
        summary_overview="",
        shots=[ComposerShotOutput(description="<Subject 1> raises a hand.")],
    )
    with pytest.raises(ValueError, match="leaked internal subject aliases"):
        validate_composer_output(inputs, prose)


def test_i2va_renderer_keeps_literal_first_frame():
    inputs = composer_input(authoring("i2va"))
    prose = ComposerOutput(
        subject_appearances=[],
        style_description="The video uses a 2D anime illustration style.",
        summary_overview="",
        shots=[
            ComposerShotOutput(
                description="A silver-haired woman stands with both hands lowered, then removes her glasses. The camera remains fixed."
            )
        ],
    )
    validate_composer_output(inputs, prose)
    prompt = render_i2va(prose, inputs)
    assert prompt.startswith("For the target video, at 0.00 seconds")
    assert "<Subject " not in prompt


def test_structured_schema_has_descriptions_for_qwen_contract():
    schema = InitialPicturePayload.model_json_schema()
    assert schema["description"] == "One-pass Qwen output for an I2VA first frame or Ref2VA Initial image."
    assert schema["$defs"]["InitialSubjectDraft"]["description"].startswith("Appearance prose")
    assert ComposerOutput.model_json_schema()["description"].startswith("Natural English content only")


def test_qwen_parser_rejects_thinking_and_non_object():
    with pytest.raises(ValueError, match="thinking block"):
        parse_model_json('<think>hidden</think>{"appearance_ja":"銀髪"}', CastPicturePayload)
    with pytest.raises(ValueError, match="exactly one JSON object"):
        parse_model_json('[{"appearance_ja":"銀髪"}]', CastPicturePayload)


def test_qwen_parser_tolerates_one_outer_json_fence():
    parsed = parse_model_json('```json\n{"appearance_ja":"銀髪"}\n```', CastPicturePayload)
    assert parsed.appearance_ja == "銀髪"


def test_ref2va_without_initial_uses_cast_only_and_empty_style():
    analysis = canonicalize_reference_analysis(
        mode="ref2va",
        roles=["cast", "cast"],
        payloads=[
            CastPicturePayload(appearance_ja="銀髪。"),
            CastPicturePayload(appearance_ja="黒髪。"),
        ],
    )
    assert analysis.initial_picture_number is None
    assert analysis.initial_ja == ""
    assert analysis.style_ja == ""
    assert [subject.label for subject in analysis.subjects] == ["<Subject 1>", "<Subject 2>"]


def test_authoring_rejects_client_rewrite_of_picture_provenance():
    with pytest.raises(ValidationError, match="code-owned role layout"):
        AuthoringInput(
            mode="ref2va",
            reference_image_count=2,
            initial_picture_number=1,
            subjects=[
                AuthoringSubject(
                    label="<Subject 1>",
                    picture_number=2,
                    source_role="cast",
                    appearance_ja="銀髪。",
                ),
                AuthoringSubject(
                    label="<Subject 2>",
                    picture_number=1,
                    source_role="initial",
                    appearance_ja="黒髪。",
                ),
            ],
            shots=[UserShot()],
        )


def test_authoring_rejects_subject_labels_inside_appearance_or_style():
    with pytest.raises(ValidationError, match="Appearance text"):
        AuthoringInput(
            mode="i2va",
            reference_image_count=1,
            initial_picture_number=1,
            subjects=[
                AuthoringSubject(
                    label="<Subject 1>",
                    picture_number=1,
                    source_role="initial",
                    appearance_ja="<Subject 1>は銀髪。",
                )
            ],
            shots=[UserShot()],
        )

    with pytest.raises(ValidationError, match="Style text"):
        AuthoringInput(
            mode="i2va",
            reference_image_count=1,
            initial_picture_number=1,
            subjects=[],
            style_ja="<Subject 1>のアニメ調。",
            shots=[UserShot()],
        )



def test_authoring_allows_temporary_unknown_subject_reference_but_compose_rejects_it():
    authoring = AuthoringInput(
        mode="ref2va",
        reference_image_count=1,
        initial_picture_number=None,
        subjects=[
            AuthoringSubject(
                label="<Subject 1>",
                picture_number=1,
                source_role="cast",
                appearance_ja="銀髪。",
            )
        ],
        shots=[UserShot(motion="<Subject 2>が右手を上げる。")],
    )

    with pytest.raises(ValueError, match=r"undefined canonical Subjects: <Subject 2>"):
        composer_input(authoring)

def test_ref2va_renderer_omits_initial_anchor_and_style_when_absent():
    authoring_input = AuthoringInput(
        mode="ref2va",
        reference_image_count=1,
        initial_picture_number=None,
        subjects=[
            AuthoringSubject(
                label="<Subject 1>",
                picture_number=1,
                source_role="cast",
                appearance_ja="長い銀髪。",
            )
        ],
        shots=[UserShot(motion="<Subject 1>が右手を上げる。")],
    )
    inputs = composer_input(authoring_input)
    prose = ComposerOutput(
        subject_appearances=[
            ComposerSubjectAppearance(label="<Subject 1>", appearance_en="long silver hair")
        ],
        style_description="",
        summary_overview="<Subject 1> performs the requested action.",
        shots=[ComposerShotOutput(description="<Subject 1> performs the requested action.")],
    )
    validate_composer_output(inputs, prose)
    prompt = render_ref2va(prose, inputs)
    assert "weak composition reference" not in prompt
    assert "weak_reference" not in prompt
    assert "visual style" not in prompt.casefold()


def test_ref2va_scene_only_initial_picture_needs_no_subject():
    authoring_input = AuthoringInput(
        mode="ref2va",
        reference_image_count=1,
        initial_picture_number=1,
        subjects=[],
        initial_ja="夕暮れの濡れた屋上。",
        style_ja="アニメ調。",
        shots=[UserShot(motion="雨がゆっくり降る。")],
    )
    inputs = composer_input(authoring_input)
    prose = ComposerOutput(
        subject_appearances=[],
        style_description="The target video uses a 2D anime illustration style.",
        summary_overview="A rainy rooftop scene develops at sunset.",
        shots=[
            ComposerShotOutput(
                description="A wet rooftop at sunset remains in view as rain falls slowly."
            )
        ],
    )
    validate_composer_output(inputs, prose)
    prompt = render_ref2va(prose, inputs)
    assert "<Picture 1> is a weak composition reference" in prompt
    assert "weak_reference" in prompt
    assert "fully_preserved" not in prompt


def test_multishot_timestamps_are_rendered_code_owned():
    authoring_input = AuthoringInput(
        mode="ref2va",
        reference_image_count=1,
        initial_picture_number=None,
        subjects=[
            AuthoringSubject(
                label="<Subject 1>", picture_number=1, source_role="cast", appearance_ja="銀髪。"
            )
        ],
        shots=[
            UserShot(motion="<Subject 1>が歩く。"),
            UserShot(start_time_seconds=3.5, motion="<Subject 1>が振り向く。", camera="Slow pan right"),
        ],
    )
    inputs = composer_input(authoring_input)
    prose = ComposerOutput(
        subject_appearances=[ComposerSubjectAppearance(label="<Subject 1>", appearance_en="silver hair")],
        summary_overview="<Subject 1> walks and then turns around.",
        shots=[
            ComposerShotOutput(description="<Subject 1> walks forward."),
            ComposerShotOutput(description="<Subject 1> turns around during a slow pan right."),
        ],
    )
    validate_composer_output(inputs, prose)
    prompt = render_ref2va(prose, inputs)
    assert "[Shot 1] <Subject 1> walks forward." in prompt
    assert "[Shot 2] At 00:03.500, <Subject 1> turns around" in prompt
