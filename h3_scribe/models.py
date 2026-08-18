"""Pydantic contracts for H3 semantic authoring."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["i2va", "ref2va"]
PictureRole = Literal["initial", "cast"]
DEFAULT_CAMERA = "Fixed camera"
MAX_REFERENCE_IMAGES = 9
MAX_SUBJECTS_PER_PICTURE = 8
MAX_SHOTS = 8
MAX_STRUCTURED_TEXT_CHARS = 16_000
MAX_SHOT_START_SECONDS = 15.0
LOCAL_SUBJECT_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_])subject_(0|[1-9][0-9]*)(?![A-Za-z0-9_])"
)
CANONICAL_SUBJECT_IN_TEXT_RE = re.compile(r"<Subject ([1-9][0-9]*)>")


class InitialSubjectDraft(BaseModel):
    """Appearance prose for one person/character discovered in an Initial image."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    appearance_ja: str = Field(min_length=1)


class InitialPicturePayload(BaseModel):
    """One-pass Qwen output for an I2VA first frame or Ref2VA Initial image."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subjects: list[InitialSubjectDraft] = Field(
        default_factory=list, max_length=MAX_SUBJECTS_PER_PICTURE
    )
    initial_ja: str = ""
    style_ja: str = ""

    @model_validator(mode="after")
    def validate_local_subject_references(self) -> "InitialPicturePayload":
        known = {f"subject_{index}" for index in range(len(self.subjects))}
        refs = {match.group(0) for match in LOCAL_SUBJECT_IN_TEXT_RE.finditer(self.initial_ja)}
        unknown = refs - known
        if unknown:
            raise ValueError(
                "Initial description references unknown local subjects: "
                + ", ".join(sorted(unknown))
            )
        return self


class CastPicturePayload(BaseModel):
    """One-pass Qwen output for a Cast image, whose contract is exactly one person."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    appearance_ja: str = Field(min_length=1)


class CanonicalSubject(BaseModel):
    """Python-owned global H3 Subject with editable Japanese target appearance."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(pattern=r"^<Subject [1-9][0-9]*>$")
    picture_number: int = Field(ge=1, le=MAX_REFERENCE_IMAGES)
    source_role: PictureRole
    appearance_ja: str = Field(min_length=1)


class ReferenceAnalysis(BaseModel):
    """Image-only semantic draft shown to the user before composition."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Mode
    reference_image_count: int = Field(ge=1, le=MAX_REFERENCE_IMAGES)
    initial_picture_number: int | None = Field(default=None, ge=1, le=MAX_REFERENCE_IMAGES)
    picture_roles: list[PictureRole] = Field(min_length=1, max_length=MAX_REFERENCE_IMAGES)
    subjects: list[CanonicalSubject] = Field(default_factory=list)
    initial_ja: str = ""
    style_ja: str = ""

    @model_validator(mode="after")
    def validate_reference_layout(self) -> "ReferenceAnalysis":
        if len(self.picture_roles) != self.reference_image_count:
            raise ValueError("picture role count must equal reference image count")
        initial_indexes = [
            index for index, role in enumerate(self.picture_roles, start=1) if role == "initial"
        ]
        if self.mode == "i2va":
            if self.reference_image_count != 1 or self.initial_picture_number != 1:
                raise ValueError("I2VA requires Picture 1 as its single Initial/first-frame image")
            if initial_indexes != [1]:
                raise ValueError("I2VA Picture 1 must have the initial role")
        else:
            expected = [] if self.initial_picture_number is None else [self.initial_picture_number]
            if initial_indexes != expected:
                raise ValueError("Ref2VA picture roles do not match initial_picture_number")
        return self


class UserShot(BaseModel):
    """One user-authored editorial shot. A later shot always represents a cut."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    start_time_seconds: float | None = Field(default=None, ge=0, le=MAX_SHOT_START_SECONDS)
    motion: str = ""
    camera: str = DEFAULT_CAMERA


class AuthoringSubject(BaseModel):
    """User-editable target appearance for one canonical Subject."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(pattern=r"^<Subject [1-9][0-9]*>$")
    picture_number: int = Field(ge=1, le=MAX_REFERENCE_IMAGES)
    source_role: PictureRole
    appearance_ja: str = Field(min_length=1)


class AuthoringInput(BaseModel):
    """Authoritative semantic input after the user edits Qwen's Japanese drafts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Mode
    reference_image_count: int = Field(ge=1, le=MAX_REFERENCE_IMAGES)
    initial_picture_number: int | None = Field(default=None, ge=1, le=MAX_REFERENCE_IMAGES)
    subjects: list[AuthoringSubject] = Field(default_factory=list)
    initial_ja: str = ""
    style_ja: str = ""
    throughout: str = ""
    shots: list[UserShot] = Field(
        default_factory=lambda: [UserShot()], min_length=1, max_length=MAX_SHOTS
    )

    @model_validator(mode="after")
    def validate_structure(self) -> "AuthoringInput":
        if self.mode == "i2va" and (
            self.reference_image_count != 1 or self.initial_picture_number != 1
        ):
            raise ValueError("I2VA requires exactly Picture 1 as the first frame")
        if (
            self.mode == "ref2va"
            and self.initial_picture_number is not None
            and self.initial_picture_number > self.reference_image_count
        ):
            raise ValueError("initial_picture_number exceeds reference image count")

        if self.shots[0].start_time_seconds is not None:
            raise ValueError("Shot 1 must not have a start timestamp")
        previous = 0.0
        for index, shot in enumerate(self.shots[1:], start=2):
            if shot.start_time_seconds is None:
                raise ValueError(f"Shot {index} requires a start timestamp")
            if shot.start_time_seconds <= previous:
                raise ValueError("later shot timestamps must be strictly increasing")
            previous = shot.start_time_seconds

        labels = [subject.label for subject in self.subjects]
        if len(labels) != len(set(labels)):
            raise ValueError("Subject labels must be unique")
        expected_labels = [f"<Subject {index}>" for index in range(1, len(labels) + 1)]
        if labels != expected_labels:
            raise ValueError("Subject labels must be consecutive and Python-owned")
        if any(subject.picture_number > self.reference_image_count for subject in self.subjects):
            raise ValueError("Subject picture_number exceeds reference image count")

        cast_picture_count = self.reference_image_count - (1 if self.initial_picture_number else 0)
        initial_subject_count = len(self.subjects) - cast_picture_count
        if initial_subject_count < 0:
            raise ValueError("Subject count is smaller than the required Cast Picture count")
        expected_provenance: list[tuple[int, PictureRole]] = []
        for picture_number in range(1, self.reference_image_count + 1):
            if picture_number == self.initial_picture_number:
                expected_provenance.extend(
                    (picture_number, "initial") for _ in range(initial_subject_count)
                )
            else:
                expected_provenance.append((picture_number, "cast"))
        actual_provenance = [
            (subject.picture_number, subject.source_role) for subject in self.subjects
        ]
        if actual_provenance != expected_provenance:
            raise ValueError("Subject Picture provenance does not match the code-owned role layout")

        for subject in self.subjects:
            if CANONICAL_SUBJECT_IN_TEXT_RE.search(subject.appearance_ja):
                raise ValueError("Appearance text must not contain canonical Subject labels")
        if CANONICAL_SUBJECT_IN_TEXT_RE.search(self.style_ja):
            raise ValueError("Style text must not contain canonical Subject labels")

        semantic_text = "\n".join(
            [
                self.initial_ja,
                self.style_ja,
                self.throughout,
                *(shot.motion for shot in self.shots),
                *(shot.camera for shot in self.shots),
            ]
        )
        total = len(semantic_text) + sum(len(subject.appearance_ja) for subject in self.subjects)
        if total > MAX_STRUCTURED_TEXT_CHARS:
            raise ValueError(
                f"structured semantic input exceeds {MAX_STRUCTURED_TEXT_CHARS} characters"
            )
        return self


class ComposerSubjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(pattern=r"^<Subject [1-9][0-9]*>$")
    picture_number: int = Field(ge=1, le=MAX_REFERENCE_IMAGES)
    source_role: PictureRole
    appearance_ja: str = Field(min_length=1)


class ComposerShotInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    start_time_seconds: float | None = None
    motion: str = ""
    camera: str = DEFAULT_CAMERA
    throughout: str = ""


class ComposerInput(BaseModel):
    """Japanese semantic specification handed to the one text-only Composer call."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Mode
    initial_picture_number: int | None = None
    subjects: list[ComposerSubjectInput] = Field(default_factory=list)
    initial_ja: str = ""
    style_ja: str = ""
    shots: list[ComposerShotInput] = Field(min_length=1, max_length=MAX_SHOTS)


class ComposerSubjectAppearance(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(pattern=r"^<Subject [1-9][0-9]*>$")
    appearance_en: str = Field(min_length=1)


class ComposerShotOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(min_length=1)


class ComposerOutput(BaseModel):
    """Natural English content only; all H3 protocol syntax stays code-owned."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject_appearances: list[ComposerSubjectAppearance] = Field(default_factory=list)
    style_description: str = ""
    summary_overview: str = ""
    shots: list[ComposerShotOutput] = Field(min_length=1, max_length=MAX_SHOTS)
