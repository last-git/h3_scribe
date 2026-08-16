"""Deterministic MiniMax H3 protocol rendering."""

from __future__ import annotations

from .models import ComposerInput, ComposerOutput

def _timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    minutes, remainder = divmod(milliseconds, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def _render_shots(prose: ComposerOutput, inputs: ComposerInput) -> str:
    if len(prose.shots) != len(inputs.shots):
        raise ValueError("Composer output shot count does not match Composer input")
    rendered: list[str] = []
    for index, (shot_prose, shot_input) in enumerate(zip(prose.shots, inputs.shots), start=1):
        description = shot_prose.description.strip()
        if index == 1:
            rendered.append(f"[Shot 1] {description}")
            continue
        if shot_input.start_time_seconds is None:
            raise ValueError(f"Shot {index} has no start timestamp")
        rendered.append(
            f"[Shot {index}] At {_timestamp(shot_input.start_time_seconds)}, {description}"
        )
    return "\n\n".join(rendered)


def _appearance_map(prose: ComposerOutput) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in prose.subject_appearances:
        if item.label in result:
            raise ValueError(f"Composer duplicated appearance for {item.label}")
        result[item.label] = item.appearance_en.strip().rstrip(".;")
    return result


def render_i2va(
    prose: ComposerOutput,
    inputs: ComposerInput,
    *,
    soundscape: str = "Natural synchronized sound.",
) -> str:
    detail: list[str] = []
    if prose.style_description:
        detail.append(prose.style_description.strip())
    detail.append(_render_shots(prose, inputs))
    return (
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
        "integrated_multimodal_description:\n"
        + "\n".join(detail)
        + "\n\noverall_soundscape:\n"
        + soundscape
        + "\n\nnon_diegetic_music:\nN/A"
    )


def _subject_mapping_sentence(inputs: ComposerInput) -> str:
    by_picture: dict[int, list[str]] = {}
    for subject in inputs.subjects:
        by_picture.setdefault(subject.picture_number, []).append(subject.label)
    clauses: list[str] = []
    for picture_number in sorted(by_picture):
        labels = by_picture[picture_number]
        if len(labels) == 1:
            joined = labels[0]
        elif len(labels) == 2:
            joined = f"{labels[0]} and {labels[1]}"
        else:
            joined = ", ".join(labels[:-1]) + f", and {labels[-1]}"
        clauses.append(f"<Picture {picture_number}> provides the visual reference for {joined}")
    if inputs.initial_picture_number is not None:
        clauses.append(
            f"<Picture {inputs.initial_picture_number}> also provides weak composition guidance "
            "for the opening of [Shot 1]"
        )
    if not clauses:
        return "[reference generation] The supplied references guide the target video."
    return "[reference generation] " + "; ".join(clauses) + "."


def render_ref2va(
    prose: ComposerOutput,
    inputs: ComposerInput,
    *,
    soundscape: str = "Natural synchronized sound.",
) -> str:
    if not prose.summary_overview:
        raise ValueError("Ref2VA Composer must return summary_overview")
    if not inputs.subjects and inputs.initial_picture_number is None:
        raise ValueError("Ref2VA requires at least a Cast Subject or an Initial image")

    appearance = _appearance_map(prose)
    expected = {subject.label for subject in inputs.subjects}
    if set(appearance) != expected:
        raise ValueError("Composer subject appearance output does not match canonical Subjects")

    definitions: list[str] = []
    retentions: list[str] = []
    if inputs.initial_picture_number is not None:
        picture = inputs.initial_picture_number
        definitions.append(
            f"<Picture {picture}> is a weak composition reference for the opening of [Shot 1], "
            "providing overall viewpoint, framing, scene layout, and subject placement."
        )
        retentions.append(
            f"<Picture {picture}> ([Shot 1] composition reference): weak_reference - "
            "use its overall composition as guidance while allowing the target Initial "
            "description to define the opening state."
        )

    for subject in inputs.subjects:
        definitions.append(
            f"{subject.label} is the referenced person or character shown in "
            f"<Picture {subject.picture_number}>. Target appearance: {appearance[subject.label]}."
        )
        retentions.append(
            f"{subject.label}: fully_preserved - preserve the reference role and target "
            f"appearance defined for {subject.label}."
        )

    detail: list[str] = []
    if prose.style_description:
        detail.append(prose.style_description.strip())
    if inputs.initial_picture_number is not None:
        detail.append(
            f"The opening composition is weakly guided by <Picture {inputs.initial_picture_number}>."
        )
    detail.append(_render_shots(prose, inputs))

    return (
        "subject_definitions:\n"
        + "\n".join(definitions)
        + "\n\nsummary:\n"
        + _subject_mapping_sentence(inputs)
        + " "
        + prose.summary_overview.strip()
        + "\n\nretention_analysis:\n"
        + "\n".join(retentions)
        + "\n\ndetailed_description:\n"
        + "\n".join(detail)
        + "\n\noverall_soundscape:\n"
        + soundscape
        + "\n\nnon_diegetic_music:\nN/A"
    )
