"""Deterministic H3 semantic canonicalization and Composer validation."""

from __future__ import annotations

import re

from .models import (
    AuthoringInput, AuthoringSubject, CANONICAL_SUBJECT_IN_TEXT_RE, CanonicalSubject,
    CastPicturePayload, ComposerInput, ComposerOutput, ComposerShotInput,
    ComposerSubjectInput, InitialPicturePayload, LOCAL_SUBJECT_IN_TEXT_RE, MAX_REFERENCE_IMAGES,
    Mode, PictureRole, ReferenceAnalysis, UserShot,
)

def _replace_local_subjects(text: str, mapping: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(0)
        if key not in mapping:
            raise ValueError(f"unknown local subject reference: {key}")
        return mapping[key]

    return LOCAL_SUBJECT_IN_TEXT_RE.sub(replace, text)


def picture_roles(
    mode: Mode, reference_image_count: int, initial_picture_number: int | None
) -> list[PictureRole]:
    if not 1 <= reference_image_count <= MAX_REFERENCE_IMAGES:
        raise ValueError(f"reference image count must be 1..{MAX_REFERENCE_IMAGES}")
    if mode == "i2va":
        if reference_image_count != 1:
            raise ValueError("I2VA accepts exactly one first-frame image")
        if initial_picture_number not in (None, 1):
            raise ValueError("I2VA Initial image is always Picture 1")
        return ["initial"]
    if initial_picture_number is not None and not 1 <= initial_picture_number <= reference_image_count:
        raise ValueError("Ref2VA initial_picture_number is outside the uploaded image range")
    return [
        "initial" if initial_picture_number == picture_number else "cast"
        for picture_number in range(1, reference_image_count + 1)
    ]


def canonicalize_reference_analysis(
    *,
    mode: Mode,
    roles: list[PictureRole],
    payloads: list[InitialPicturePayload | CastPicturePayload],
) -> ReferenceAnalysis:
    if len(roles) != len(payloads):
        raise ValueError("role/payload counts differ")
    if not roles:
        raise ValueError("at least one reference image is required")
    initial_indexes = [index for index, role in enumerate(roles, start=1) if role == "initial"]
    if mode == "i2va" and initial_indexes != [1]:
        raise ValueError("I2VA must analyze Picture 1 as its Initial image")
    if mode == "ref2va" and len(initial_indexes) > 1:
        raise ValueError("Ref2VA supports at most one Initial image")

    subjects: list[CanonicalSubject] = []
    initial_text = ""
    style_text = ""
    next_subject = 1
    for picture_number, (role, payload) in enumerate(zip(roles, payloads), start=1):
        if role == "initial":
            if not isinstance(payload, InitialPicturePayload):
                raise ValueError("Initial role requires InitialPicturePayload")
            mapping: dict[str, str] = {}
            for local_index, subject in enumerate(payload.subjects):
                label = f"<Subject {next_subject}>"
                next_subject += 1
                mapping[f"subject_{local_index}"] = label
                subjects.append(
                    CanonicalSubject(
                        label=label,
                        picture_number=picture_number,
                        source_role="initial",
                        appearance_ja=subject.appearance_ja,
                    )
                )
            initial_text = _replace_local_subjects(payload.initial_ja, mapping)
            style_text = payload.style_ja
        else:
            if not isinstance(payload, CastPicturePayload):
                raise ValueError("Cast role requires CastPicturePayload")
            label = f"<Subject {next_subject}>"
            next_subject += 1
            subjects.append(
                CanonicalSubject(
                    label=label,
                    picture_number=picture_number,
                    source_role="cast",
                    appearance_ja=payload.appearance_ja,
                )
            )

    initial_picture_number = initial_indexes[0] if initial_indexes else None
    return ReferenceAnalysis(
        mode=mode,
        reference_image_count=len(roles),
        initial_picture_number=initial_picture_number,
        picture_roles=roles,
        subjects=subjects,
        initial_ja=initial_text,
        style_ja=style_text,
    )


def authoring_draft(analysis: ReferenceAnalysis) -> AuthoringInput:
    return AuthoringInput(
        mode=analysis.mode,
        reference_image_count=analysis.reference_image_count,
        initial_picture_number=analysis.initial_picture_number,
        subjects=[
            AuthoringSubject.model_validate(subject.model_dump()) for subject in analysis.subjects
        ],
        initial_ja=analysis.initial_ja,
        style_ja=analysis.style_ja,
        throughout="",
        shots=[UserShot()],
    )


def _validate_authoring_subject_references(authoring: AuthoringInput) -> None:
    """Require canonical Subject references to resolve at the Compose boundary.

    Authoring deliberately permits temporary dangling references after re-analysis so the
    user can repair preserved Motion / Camera / Throughout text before composing.
    """
    semantic_text = "\n".join(
        [
            authoring.initial_ja,
            authoring.style_ja,
            authoring.throughout,
            *(shot.motion for shot in authoring.shots),
            *(shot.camera for shot in authoring.shots),
        ]
    )
    known = {subject.label for subject in authoring.subjects}
    refs = {match.group(0) for match in CANONICAL_SUBJECT_IN_TEXT_RE.finditer(semantic_text)}
    unknown = refs - known
    if unknown:
        raise ValueError(
            "Authoring references undefined canonical Subjects: "
            + ", ".join(sorted(unknown))
            + ". Edit Authoring before ② COMPOSE."
        )


def composer_input(authoring: AuthoringInput) -> ComposerInput:
    _validate_authoring_subject_references(authoring)
    return ComposerInput(
        mode=authoring.mode,
        initial_picture_number=authoring.initial_picture_number,
        subjects=[
            ComposerSubjectInput.model_validate(subject.model_dump()) for subject in authoring.subjects
        ],
        initial_ja=authoring.initial_ja,
        style_ja=authoring.style_ja,
        shots=[
            ComposerShotInput(
                start_time_seconds=shot.start_time_seconds,
                motion=shot.motion,
                camera=shot.camera,
                throughout=authoring.throughout,
            )
            for shot in authoring.shots
        ],
    )


def validate_composer_output(inputs: ComposerInput, output: ComposerOutput) -> ComposerOutput:
    if len(output.shots) != len(inputs.shots):
        raise ValueError(
            f"Composer changed the shot count: expected {len(inputs.shots)}, got {len(output.shots)}."
        )
    expected_labels = {subject.label for subject in inputs.subjects}
    appearance_labels = {item.label for item in output.subject_appearances}
    if len(output.subject_appearances) != len(appearance_labels):
        raise ValueError("Composer duplicated a Subject appearance output.")
    if inputs.mode == "ref2va":
        if appearance_labels != expected_labels:
            raise ValueError("Ref2VA Composer subject_appearances do not match canonical Subjects.")
        if not output.summary_overview:
            raise ValueError("Ref2VA Composer returned an empty summary_overview.")
    else:
        if output.subject_appearances:
            raise ValueError("I2VA Composer must keep subject_appearances empty.")
        if output.summary_overview:
            raise ValueError("I2VA Composer must keep summary_overview empty.")
    if bool(inputs.style_ja) != bool(output.style_description):
        raise ValueError("Composer changed whether Style is present.")

    emitted_text = "\n".join(
        [
            output.summary_overview,
            output.style_description,
            *(item.appearance_en for item in output.subject_appearances),
            *(shot.description for shot in output.shots),
        ]
    )
    emitted_labels = {
        match.group(0) for match in re.finditer(r"<Subject [1-9][0-9]*>", emitted_text)
    }
    if inputs.mode == "i2va" and emitted_labels:
        raise ValueError(
            "I2VA Composer leaked internal subject aliases: " + ", ".join(sorted(emitted_labels))
        )
    unknown = emitted_labels - expected_labels
    if inputs.mode == "ref2va" and unknown:
        raise ValueError(
            "Ref2VA Composer invented unknown subject labels: " + ", ".join(sorted(unknown))
        )
    return output
