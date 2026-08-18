#!/usr/bin/env python3
"""Opt-in real-Qwen Composer and Analyze quality diagnostics for H3 Scribe.

Real runs go through a running ComfyUI via its standard HTTP APIs, then through
the installed H3 Scribe nodes and SimpleQwenVLggufV2 node. This deliberately
does not import or call Simple Qwen's inference runtime directly.

The semantic sentinels are human-readable regression checks, not an LLM judge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Model output may contain characters outside Windows' legacy console code page.
for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

# Make `python quality/run.py` work from a source checkout without installation.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from h3_scribe.models import (  # noqa: E402
    AuthoringInput,
    AuthoringSubject,
    CastPicturePayload,
    ComposerInput,
    ComposerOutput,
    InitialPicturePayload,
    UserShot,
)
from h3_scribe.requests import cast_request, compose_request, initial_request  # noqa: E402
from h3_scribe.semantics import composer_input, validate_composer_output  # noqa: E402
from h3_scribe.serialization import parse_model_json  # noqa: E402


DEFAULT_MODEL = "Qwen3.6-27B-Uncensored-HauhauCS-Balanced-Q3_K_P.gguf"
DEFAULT_MMPROJ = "mmproj-Qwen3.6-27B-Uncensored-HauhauCS-Balanced-f16.gguf"
DEFAULT_WORKFLOW = ROOT / "quality" / "workflows" / "composer_quality.json"
DEFAULT_ANALYZE_WORKFLOW = ROOT / "quality" / "workflows" / "analyze_quality.json"
INITIAL_FIXTURE = ROOT / "quality" / "fixtures" / "builder_quality" / "two_silver_black.png"
CAST_FIXTURE = ROOT / "quality" / "fixtures" / "builder_quality" / "single_brown.png"
SYNTHETIC_TWO_FIXTURE = ROOT / "quality" / "fixtures" / "multi_reference" / "two_subjects.png"
SYNTHETIC_RED_FIXTURE = ROOT / "quality" / "fixtures" / "multi_reference" / "single_red.png"
SYNTHETIC_BLUE_FIXTURE = ROOT / "quality" / "fixtures" / "multi_reference" / "single_blue.png"


@dataclass(frozen=True)
class RequiredConcept:
    name: str
    alternatives: tuple[str, ...]
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    name: str
    authoring: AuthoringInput
    required_by_shot: tuple[tuple[RequiredConcept, ...], ...] = ()
    forbidden_by_shot: tuple[tuple[RequiredConcept, ...], ...] = ()


@dataclass(frozen=True)
class AnalyzeCase:
    name: str
    kind: str
    fixture: Path
    expected_subjects: int
    subject_concepts: tuple[tuple[RequiredConcept, ...], ...]
    require_initial_subject_refs: bool = False
    require_style: bool = False
    forbidden_appearance_terms: tuple[str, ...] = ()


def concept(
    name: str,
    *alternatives: str,
    patterns: tuple[str, ...] = (),
) -> RequiredConcept:
    return RequiredConcept(name=name, alternatives=alternatives, patterns=patterns)


SILVER_HAIR = concept(
    "silver/white hair",
    "銀髪", "銀色", "シルバー", "白髪", "白い髪", "灰色", "グレー",
    "silver hair", "white hair", "gray hair", "grey hair",
)
BLACK_HAIR = concept(
    "black/dark hair",
    "黒髪", "黒い髪", "黒の髪", "黒色の髪", "黒い短髪", "黒の短髪",
    "黒色の短髪", "黒いショートヘア", "黒のショートヘア", "ブラックヘア",
    "ブラックの髪", "ダークヘア", "black hair", "dark hair",
)
RED_HAIR = concept(
    "red/reddish hair",
    "赤髪", "赤い髪", "赤毛", "赤茶", "赤褐色", "栗色", "auburn", "red hair",
)
BLUE_HAIR = concept(
    "blue hair",
    "青髪", "青い髪", "青色の髪", "青い短髪", "青色の短髪", "青いショートヘア",
    "ブルーの髪", "blue hair",
)
BROWN_HAIR = concept(
    "brown/reddish hair",
    "茶髪", "茶色", "ブラウン", "赤褐色", "赤茶", "栗色", "auburn", "brown hair",
)
GLASSES = concept("glasses", "眼鏡", "メガネ", "めがね", "グラス", "glasses")
STAR_ACCESSORY = concept("star hair accessory", "星", "スター", "star")
CRESCENT_ACCESSORY = concept("crescent/moon hair accessory", "三日月", "月", "ムーン", "クレセント", "crescent", "moon")
YELLOW_CLOTHING = concept("yellow/gold clothing", "黄色", "黄土色", "からし色", "マスタード", "ゴールド", "yellow", "mustard", "gold")
PURPLE_CLOTHING = concept("purple clothing", "紫", "パープル", "purple", "violet")

CAST_STATE_SCENE_TERMS = (
    "本を持", "本を抱", "図書館", "背景", "立って", "座って", "歩いて", "手を伸ば",
)

ANALYZE_CASES: list[AnalyzeCase] = [
    AnalyzeCase(
        "initial_two_silver_black",
        "initial",
        INITIAL_FIXTURE,
        expected_subjects=2,
        subject_concepts=((SILVER_HAIR, STAR_ACCESSORY), (BLACK_HAIR, GLASSES)),
        require_initial_subject_refs=True,
        require_style=True,
    ),
    AnalyzeCase(
        "cast_single_brown",
        "cast",
        CAST_FIXTURE,
        expected_subjects=1,
        subject_concepts=((BROWN_HAIR, CRESCENT_ACCESSORY),),
        forbidden_appearance_terms=CAST_STATE_SCENE_TERMS,
    ),
    AnalyzeCase(
        "initial_synthetic_two_subjects",
        "initial",
        SYNTHETIC_TWO_FIXTURE,
        expected_subjects=2,
        subject_concepts=((SILVER_HAIR, STAR_ACCESSORY), (BLACK_HAIR, GLASSES)),
        require_initial_subject_refs=True,
        require_style=True,
    ),
    AnalyzeCase(
        "cast_synthetic_red",
        "cast",
        SYNTHETIC_RED_FIXTURE,
        expected_subjects=1,
        subject_concepts=((RED_HAIR, YELLOW_CLOTHING),),
        forbidden_appearance_terms=CAST_STATE_SCENE_TERMS,
    ),
    AnalyzeCase(
        "cast_synthetic_blue",
        "cast",
        SYNTHETIC_BLUE_FIXTURE,
        expected_subjects=1,
        subject_concepts=((BLUE_HAIR, GLASSES, PURPLE_CLOTHING),),
        forbidden_appearance_terms=CAST_STATE_SCENE_TERMS,
    ),
]


def subject(
    label: str = "<Subject 1>",
    *,
    picture: int = 1,
    role: str = "initial",
    appearance: str,
) -> AuthoringSubject:
    return AuthoringSubject(
        label=label,
        picture_number=picture,
        source_role=role,
        appearance_ja=appearance,
    )


def ref_authoring(
    *,
    subjects: list[AuthoringSubject],
    initial_ja: str = "",
    style_ja: str = "",
    throughout: str = "",
    shots: list[UserShot],
    reference_image_count: int | None = None,
    initial_picture_number: int | None = 1,
) -> AuthoringInput:
    if reference_image_count is None:
        reference_image_count = max((s.picture_number for s in subjects), default=1)
    return AuthoringInput(
        mode="ref2va",
        reference_image_count=reference_image_count,
        initial_picture_number=initial_picture_number,
        subjects=subjects,
        initial_ja=initial_ja,
        style_ja=style_ja,
        throughout=throughout,
        shots=shots,
    )


def fixed_camera() -> RequiredConcept:
    return concept(
        "stationary camera",
        "fixed camera",
        "camera remains fixed",
        "camera stays fixed",
        "camera is fixed",
        "camera remains stationary",
        "camera does not move",
        "stationary camera",
        "fixed shot",
        "locked off",
        "locked-off",
        patterns=(
            r"\bcamera\b[^.!?]{0,180},\s*(?:remain(?:s|ing)?|stay(?:s|ing)?|is)\s+(?:completely\s+)?(?:fixed|stationary)\b",
        ),
    )


CASES: list[Case] = [
    Case(
        "ref2va_opening_transition_fixed_camera",
        ref_authoring(
            subjects=[
                subject(
                    appearance="Long silver hair, blue eyes, and a red star hairpin."
                )
            ],
            initial_ja=(
                "<Subject 1>は両手を体の横に下ろして立っている。"
                "襟が紺色の白いシャツを着ている。背景は水色の単色。"
            ),
            style_ja="Anime style.",
            throughout="<Subject 1>はその場に立ったまま。",
            shots=[
                UserShot(
                    motion=(
                        "<Subject 1>は右手を上げ、右目の前で横向きのピースを作り、"
                        "指の間から右目が見えるようにする。同時に左目を閉じてウインクする。"
                    ),
                    camera="Fixed camera",
                )
            ],
        ),
        required_by_shot=((
            concept(
                "opening hands down",
                "both hands down",
                "both arms down",
                "arms at her sides",
                "hands at her sides",
                "arms at the sides",
                "both arms hanging by their sides",
                "both arms hanging at their sides",
                "both hands hanging by their sides",
            ),
            concept(
                "right-hand peace sign",
                "right hand peace sign",
                "peace sign with her right hand",
                patterns=(
                    r"\bright hand\b(?:(?!\bleft hand\b)[^.!?]){0,100}\b(?:peace|v) sign\b(?![^.!?]{0,40}\b(?:with|using)\b[^.!?]{0,20}\bleft hand\b)",
                    r"\b(?:peace|v) sign\b[^.!?]{0,40}\b(?:with|using)\b[^.!?]{0,20}\bright hand\b",
                ),
            ),
            concept("right eye", "right eye"),
            concept("left-eye wink", "left eye", "left-eye"),
            fixed_camera(),
            concept(
                "subject remains in place",
                "remains standing in place",
                "stays standing in place",
                "remains in place",
                "stays in place",
                "without changing position",
            ),
        ),),
    ),
    Case(
        "ref2va_simultaneous_motion_and_push_in",
        ref_authoring(
            subjects=[subject(appearance="Short dark hair and round glasses.")],
            initial_ja="<Subject 1>は両手を机の上に置いて座っている。",
            throughout="<Subject 1>は座った姿勢を保つ。",
            shots=[
                UserShot(
                    motion="<Subject 1>は右手だけをゆっくり持ち上げ、同時に左手は動かさない。",
                    camera="右手を持ち上げる間、<Subject 1>の顔へゆっくりドリーインする。",
                )
            ],
        ),
        required_by_shot=((
            concept("right hand rises", "right hand", "right-hand"),
            concept("slow motion", "slowly", "slow"),
            concept(
                "left hand remains still",
                "left hand remains still",
                "left hand stays still",
                "keeping the left hand still",
                "without moving the left hand",
                "left hand does not move",
            ),
            concept(
                "simultaneity",
                "simultaneously",
                "at the same time",
                "while",
                "as the hand rises",
                "as the hand is raised",
                "as the right hand lifts",
                "as the right hand rises",
                "as the right hand is raised",
                patterns=(
                    r"\bcamera\b[^.!?]{0,180}\b(?:doll(?:y|ies)|push(?:es)?)\b[^.!?]{0,120}\bduring (?:the|this) (?:movement|motion)\b",
                ),
            ),
            concept("dolly-in camera operation", "dolly in", "dolly-in", "dollies in", "push in", "push-in"),
            concept(
                "seated posture persists",
                "remains seated",
                "stays seated",
                "maintains a seated posture",
                "while remaining seated",
                "seated posture",
            ),
        ),),
    ),
    Case(
        "ref2va_count_direction_and_after_action_camera",
        ref_authoring(
            subjects=[subject(appearance="Long navy hair.")],
            shots=[
                UserShot(
                    motion="<Subject 1>は右手を左右に2回振った後、右手を下ろす。",
                    camera="動作が終わった後だけ、カメラをゆっくり左へパンする。",
                )
            ],
        ),
        required_by_shot=((
            concept("right-hand wave", "right hand", "right-hand"),
            concept("two waves", "twice", "two times", "two back-and-forth"),
            concept("hand lowers after wave", "lowers", "lowering", "brings her right hand down", "drops her right hand"),
            concept("camera action occurs after motion", "after", "only after", "once the action"),
            concept("slow left pan", "slowly pans left", "slow pan to the left", "slow pan left", "pans slowly to the left"),
        ),),
    ),
    Case(
        "ref2va_two_explicit_shots",
        ref_authoring(
            subjects=[subject(appearance="Long silver hair and a red star hairpin.")],
            initial_ja="<Subject 1>は正面を向いて立っている。",
            shots=[
                UserShot(
                    motion="<Subject 1>は右手を一度振る。",
                    camera="Fixed camera",
                ),
                UserShot(
                    start_time_seconds=3.0,
                    motion="<Subject 1>は左目を閉じてウインクする。",
                    camera="<Subject 1>の顔のクローズアップ。Fixed camera",
                ),
            ],
        ),
        required_by_shot=(
            (
                concept("one wave", "once", "one time", "single wave"),
                fixed_camera(),
            ),
            (
                concept("left-eye wink", "left eye", "left-eye"),
                concept("close-up framing", "close-up", "close up"),
                fixed_camera(),
            ),
        ),
    ),
    Case(
        "ref2va_two_subjects_same_picture_passive_target",
        ref_authoring(
            subjects=[
                subject(appearance="Long silver hair and a red star hairpin."),
                subject(
                    "<Subject 2>",
                    appearance="Short black hair and a black jacket.",
                ),
            ],
            initial_ja=(
                "<Subject 1>は画面左側に立ち、<Subject 2>は画面右側に立っている。"
                "<Subject 1>は<Subject 2>の左にいる。"
            ),
            shots=[
                UserShot(
                    motion="<Subject 1>が<Subject 2>の肩にゆっくり寄りかかる。",
                    camera="Fixed camera",
                )
            ],
        ),
        required_by_shot=((
            concept("subject 1 remains left", "<Subject 1>", "subject 1"),
            concept("subject 2 remains right", "<Subject 2>", "subject 2"),
            concept("leans on shoulder", "leans", "leaning"),
            concept("shoulder target", "shoulder"),
            fixed_camera(),
        ),),
    ),
    Case(
        "ref2va_subjects_from_two_pictures",
        ref_authoring(
            subjects=[
                subject(appearance="Long silver hair and blue eyes."),
                subject(
                    "<Subject 2>",
                    picture=2,
                    role="cast",
                    appearance="Short auburn hair and round glasses.",
                ),
            ],
            reference_image_count=2,
            initial_picture_number=1,
            initial_ja=(
                "<Subject 1>は画面左側に立ち、<Subject 2>は画面右側に立っている。"
            ),
            style_ja="Anime style.",
            shots=[
                UserShot(
                    motion=(
                        "<Subject 1>が<Subject 2>へ右手を差し出す。"
                        "<Subject 2>はその手を右手で握る。"
                    ),
                    camera="二人を収めたミディアムショット。Fixed camera",
                )
            ],
        ),
        required_by_shot=((
            concept("both subjects", "<Subject 1>", "subject 1"),
            concept("second subject", "<Subject 2>", "subject 2"),
            concept("right-hand interaction", "right hand", "right hands", "right-hand"),
            concept("handshake/contact", "grips", "grasps", "takes", "shakes", "hand"),
            concept("medium framing", "medium view", "medium shot"),
            fixed_camera(),
        ),),
    ),
    Case(
        "i2va_picture_is_initial_two_subject_aliases",
        AuthoringInput(
            mode="i2va",
            reference_image_count=1,
            initial_picture_number=1,
            subjects=[
                subject(appearance="Long silver hair and blue eyes."),
                subject(
                    "<Subject 2>",
                    appearance="Short black hair and a black jacket.",
                ),
            ],
            initial_ja=(
                "<Subject 1>は画面左側に立っている。"
                "<Subject 2>は画面右側に座っている。"
                "<Subject 1>は<Subject 2>の左にいる。"
            ),
            style_ja="Anime style.",
            throughout="",
            shots=[
                UserShot(
                    motion=(
                        "<Subject 1>が<Subject 2>の方を向いて右手を上げる。"
                        "<Subject 2>は座ったまま。"
                    ),
                    camera="Fixed camera",
                )
            ],
        ),
        required_by_shot=((
            concept("first subject turns toward second", "turns", "turning", "faces", "toward"),
            concept("right hand rises", "right hand", "right-hand"),
            concept(
                "second person remains seated",
                "remains seated",
                "stays seated",
                "still seated",
                "continues sitting",
                "remains in their sitting position",
                "remains in a sitting position",
                "stays in their sitting position",
                "stays in a sitting position",
            ),
            fixed_camera(),
        ),),
    ),
    Case(
        "ref2va_general_persistent_constraints",
        ref_authoring(
            subjects=[subject(appearance="Short brown hair and a blue cardigan.")],
            initial_ja="<Subject 1>は机に向かって座り、開いた本を前にしている。",
            throughout="<Subject 1>は左手を机の上に置いたまま、座った姿勢を保つ。",
            shots=[
                UserShot(
                    motion="<Subject 1>は右手で開いた本を一度閉じる。",
                    camera="カメラは被写体との距離と高さを一定に保ち、被写体を追従しない。",
                )
            ],
        ),
        required_by_shot=((
            concept(
                "book closes once",
                "close the book",
                "close the open book",
                "closes the book",
                "closes the open book",
                "closing the book",
                "closing the open book",
                "shut the book",
                "shut the open book",
                "shuts the book",
                "shuts the open book",
                "shutting the book",
                "shutting the open book",
                "book closes",
                "book is closed",
            ),
            concept("one close event", "once", "one time", "a single"),
            concept("right hand closes book", "right hand", "right-hand"),
            concept(
                "camera distance remains constant",
                "constant distance",
                "distance remains constant",
                "same distance",
                "maintains its distance",
            ),
            concept(
                "camera height remains constant",
                "constant height",
                "height remains constant",
                "same height",
                "maintains its height",
            ),
            concept(
                "camera does not track subject",
                "does not track",
                "does not follow",
                "without tracking",
                "without following",
                "non-tracking",
            ),
            concept(
                "left hand remains on desk",
                "left hand on the desk",
                "left hand remains on the desk",
                "left hand stays on the desk",
                "keeping the left hand on the desk",
            ),
            concept(
                "seated posture persists",
                "remains seated",
                "stays seated",
                "maintains a seated posture",
                "while remaining seated",
                "seated posture",
            ),
        ),),
    ),
    Case(
        "ref2va_three_shots_camera_diversity",
        ref_authoring(
            subjects=[subject(appearance="Long silver hair and blue eyes.")],
            initial_ja="<Subject 1>は正面を向いて画面中央に立っている。",
            shots=[
                UserShot(motion="<Subject 1>は右手を一度振る。", camera="Fixed camera"),
                UserShot(
                    start_time_seconds=3.0,
                    motion="<Subject 1>はゆっくり顔を左へ向ける。",
                    camera="顔を左へ向けている間、カメラをゆっくり右へパンする。",
                ),
                UserShot(
                    start_time_seconds=6.0,
                    motion="<Subject 1>は一歩だけ後ろへ下がる。",
                    camera="一歩後ろへ下がる間、カメラをゆっくりドリーアウトする。",
                ),
            ],
        ),
        required_by_shot=(
            (
                concept("single right-hand wave", "right hand", "right-hand"),
                concept("one wave", "once", "one time", "single wave"),
                fixed_camera(),
            ),
            (
                concept(
                    "head turns left",
                    "turns her head left",
                    "turns their head left",
                    "turns his head left",
                    "turns her face to the left",
                    "turns their face to the left",
                    "turns his face to the left",
                    "turning her face to the left",
                    "turning their face to the left",
                    "turning his face to the left",
                    "turns to the left",
                    "faces left",
                ),
                concept("slow right pan", "slowly pans right", "slow pan to the right", "slow pan right", "pans slowly to the right"),
                concept("camera during head turn", "while", "during", "as"),
            ),
            (
                concept("one backward step", "one step back", "a step back", "one step backward", "a single step backward", "steps back once"),
                concept("dolly-out camera operation", "dolly out", "dolly-out", "dollies out", "pulls back", "pull back"),
                concept("camera during backward step", "while", "during", "as"),
            ),
        ),
        forbidden_by_shot=(
            (
                concept("future pan leaked into Shot 1", "pan right", "pans right", "pan to the right"),
                concept("future dolly leaked into Shot 1", "dolly out", "dollies out", "pulls back"),
            ),
            (
                concept("Shot 1 wave leaked into Shot 2", "wave", "waves", "waving"),
                concept("Shot 3 backward step leaked into Shot 2", "step back", "steps back", "backward step"),
            ),
            (
                concept("Shot 1 wave leaked into Shot 3", "wave", "waves", "waving"),
                concept("Shot 2 pan leaked into Shot 3", "pan right", "pans right", "pan to the right"),
            ),
        ),
    ),
    Case(
        "ref2va_throughout_and_negation_across_two_shots",
        ref_authoring(
            subjects=[subject(appearance="Short brown hair and round glasses.")],
            initial_ja="<Subject 1>は机に向かって座り、両手を机の上に置いている。",
            throughout="<Subject 1>は左手を机の上に置いたまま、座った姿勢を保つ。",
            shots=[
                UserShot(
                    motion="<Subject 1>は右手だけをゆっくり持ち上げる。左手は動かさない。",
                    camera="Fixed camera",
                ),
                UserShot(
                    start_time_seconds=4.0,
                    motion="<Subject 1>は右手だけをゆっくり机へ戻す。左手は動かさない。",
                    camera="カメラをゆっくり左へパンする。",
                ),
            ],
        ),
        required_by_shot=(
            (
                concept("right hand rises", "right hand", "right-hand"),
                concept("left hand remains on desk", "left hand on the desk", "left hand remains on the desk", "left hand stays on the desk", "keeping the left hand on the desk"),
                concept("left hand does not move", "left hand remains still", "left hand stays still", "left hand remains motionless", "left hand is motionless", "without moving the left hand", "left hand does not move"),
                concept("seated posture persists", "remains seated", "stays seated", "maintains a seated posture", "seated posture"),
                fixed_camera(),
            ),
            (
                concept("right hand returns to desk", "right hand", "right-hand"),
                concept("hand lowers/returns", "lowers", "lowering", "returns", "returning", "brings", "back to the desk", "onto the desk"),
                concept("left hand remains on desk", "left hand on the desk", "left hand remains on the desk", "left hand stays on the desk", "keeping the left hand on the desk"),
                concept("left hand does not move", "left hand remains still", "left hand stays still", "left hand remains motionless", "left hand is motionless", "without moving the left hand", "left hand does not move"),
                concept("seated posture persists", "remains seated", "stays seated", "maintains a seated posture", "seated posture"),
                concept("slow left pan", "slowly pans left", "slow pan to the left", "slow pan left", "pans slowly to the left"),
            ),
        ),
    ),
    Case(
        "ref2va_camera_timing_during_vs_after",
        ref_authoring(
            subjects=[subject(appearance="Short black hair and a white shirt.")],
            initial_ja="<Subject 1>は赤いカップの前に立っている。",
            shots=[
                UserShot(
                    motion="<Subject 1>は右手を赤いカップへ伸ばしてつかむ。",
                    camera="右手をカップへ伸ばしている間だけ、カメラをゆっくりドリーインする。",
                ),
                UserShot(
                    start_time_seconds=3.0,
                    motion="<Subject 1>は赤いカップを机に置き、右手をカップから離す。",
                    camera="右手がカップから離れた後だけ、カメラをゆっくり右へパンする。",
                ),
            ],
        ),
        required_by_shot=(
            (
                concept("right hand reaches for cup", "right hand", "right-hand"),
                concept("red cup", "red cup"),
                concept("dolly-in camera operation", "dolly in", "dolly-in", "dollies in", "push in", "push-in"),
                concept("dolly occurs during reach", "while", "during", "as"),
            ),
            (
                concept("cup placed on desk", "places the red cup", "placing the red cup", "puts the red cup", "sets the red cup", "places the cup", "placing the cup", "puts the cup", "sets the cup"),
                concept("right hand releases cup", "releases", "releasing", "lets go", "moves his right hand away", "moves her right hand away", "moves their right hand away", "right hand away"),
                concept("camera action occurs after release", "after", "only after", "once"),
                concept("slow right pan", "slowly pans right", "slow pan to the right", "slow pan right", "pans slowly to the right"),
            ),
        ),
        forbidden_by_shot=(
            (concept("future right pan leaked into Shot 1", "pan right", "pans right", "pan to the right"),),
            (concept("Shot 1 dolly leaked into Shot 2", "dolly in", "dollies in", "push in"),),
        ),
    ),
    Case(
        "ref2va_two_subjects_role_switch_two_shots",
        ref_authoring(
            subjects=[
                subject(appearance="Long silver hair and blue eyes."),
                subject("<Subject 2>", appearance="Short black hair and round glasses."),
            ],
            initial_ja="<Subject 1>は画面左、<Subject 2>は画面右に立っている。<Subject 1>は赤いボールを持っている。",
            shots=[
                UserShot(
                    motion="<Subject 1>が右手で赤いボールを<Subject 2>へ渡し、<Subject 2>は左手で受け取る。",
                    camera="Fixed camera",
                ),
                UserShot(
                    start_time_seconds=4.0,
                    motion="<Subject 2>は左手の赤いボールを頭より高く持ち上げる。<Subject 1>は両手を下ろしたまま。",
                    camera="Fixed camera",
                ),
            ],
        ),
        required_by_shot=(
            (
                concept("Subject 1 gives ball", "<Subject 1>", "Subject 1"),
                concept("Subject 1 right hand", "right hand", "right-hand"),
                concept("ball transfer", "passes", "hands", "gives", "offers", "red ball"),
                concept("Subject 2 receives", "<Subject 2>", "Subject 2"),
                concept("Subject 2 left hand", "left hand", "left-hand"),
                concept("receives ball", "receives", "takes", "accepts", "grasps", "catches"),
                fixed_camera(),
            ),
            (
                concept("Subject 2 active", "<Subject 2>", "Subject 2"),
                concept("left-hand ball raise", "left hand", "left-hand"),
                concept("ball raised above head", "above", "over", "higher than", "head"),
                concept("Subject 1 passive", "<Subject 1>", "Subject 1"),
                concept("Subject 1 hands remain down", "both hands down", "both arms down", "hands lowered", "arms lowered", "hands at their sides", "arms at their sides"),
                fixed_camera(),
            ),
        ),
        forbidden_by_shot=(
            (concept("future above-head raise leaked into Shot 1", "above the head", "above their head", "above his head", "above her head"),),
            (concept("Shot 1 transfer leaked into Shot 2", "passes the ball", "hands the ball", "gives the ball"),),
        ),
    ),
    Case(
        "ref2va_three_subjects_two_pictures_shot_routing",
        ref_authoring(
            subjects=[
                subject(appearance="Long silver hair."),
                subject("<Subject 2>", appearance="Short black hair and glasses."),
                subject("<Subject 3>", picture=2, role="cast", appearance="Short auburn hair and a red scarf."),
            ],
            reference_image_count=2,
            initial_picture_number=1,
            initial_ja="<Subject 1>は画面左、<Subject 2>は中央、<Subject 3>は画面右に立っている。",
            shots=[
                UserShot(
                    motion="<Subject 1>が<Subject 3>へゆっくり近づく。<Subject 2>はその場から動かない。",
                    camera="三人を収めたミディアムショット。Fixed camera",
                ),
                UserShot(
                    start_time_seconds=4.0,
                    motion="<Subject 2>が<Subject 3>へ右手を一度振る。<Subject 1>はその場から動かない。",
                    camera="<Subject 2>から<Subject 3>の方向へゆっくり右へパンする。",
                ),
            ],
        ),
        required_by_shot=(
            (
                concept("Subject 1 approaches Subject 3", "<Subject 1>", "Subject 1"),
                concept("Subject 3 target", "<Subject 3>", "Subject 3"),
                concept("approach", "approaches", "moves toward", "walks toward", "comes closer"),
                concept("Subject 2 remains still", "<Subject 2>", "Subject 2"),
                concept("stationary passive subject", "remains still", "stays still", "remains stationary", "stays stationary", "does not move", "remains in place", "stays in place"),
                concept("medium framing", "medium shot", "medium view"),
                fixed_camera(),
            ),
            (
                concept("Subject 2 waves", "<Subject 2>", "Subject 2"),
                concept("Subject 3 wave target", "<Subject 3>", "Subject 3"),
                concept("right-hand wave", "right hand", "right-hand"),
                concept("one wave", "once", "one time", "single wave"),
                concept("Subject 1 remains still", "<Subject 1>", "Subject 1"),
                concept("stationary passive subject", "remains still", "stays still", "remains stationary", "stays stationary", "does not move", "remains in place", "stays in place"),
                concept("slow right pan", "slowly pans right", "slow pan to the right", "slow pan right", "pans slowly to the right"),
            ),
        ),
        forbidden_by_shot=(
            (concept("future wave leaked into Shot 1", "waves", "waving", "wave to"),),
            (concept("Shot 1 approach leaked into Shot 2", "approaches", "moves toward", "walks toward", "comes closer"),),
        ),
    ),
    Case(
        "ref2va_three_step_order_count_direction",
        ref_authoring(
            subjects=[subject(appearance="Long navy hair and a white jacket.")],
            initial_ja="<Subject 1>は正面を向いて立っている。",
            shots=[
                UserShot(
                    motion="<Subject 1>は右手を上下に3回動かし、その後左を向き、その後一歩だけ後ろへ下がる。",
                    camera="すべての動作が終わった後だけ、カメラをゆっくり右へパンする。",
                )
            ],
        ),
        required_by_shot=((
            concept("right-hand repeated movement", "right hand", "right-hand"),
            concept("three repetitions", "three times", "3 times", "three repetitions"),
            concept("then turns left", "then turns left", "then turns to the left", "afterward turns left", "turns left"),
            concept("one backward step", "one step back", "a step back", "one step backward", "a single step backward", "steps back once"),
            concept("camera only after all motion", "after", "only after", "once all", "after all"),
            concept("slow right pan", "slowly pans right", "slow pan to the right", "slow pan right", "pans slowly to the right"),
        ),),
    ),
    Case(
        "i2va_two_explicit_shots_alias_and_camera",
        AuthoringInput(
            mode="i2va",
            reference_image_count=1,
            initial_picture_number=1,
            subjects=[
                subject(appearance="Long silver hair and blue eyes."),
                subject("<Subject 2>", appearance="Short black hair, round glasses, and a black jacket."),
            ],
            initial_ja="<Subject 1>は画面左側に立ち、<Subject 2>は画面右側に座っている。",
            style_ja="Anime style.",
            throughout="",
            shots=[
                UserShot(motion="<Subject 1>は右手をゆっくり上げる。", camera="Fixed camera"),
                UserShot(
                    start_time_seconds=3.5,
                    motion="<Subject 2>は立ち上がり、<Subject 1>の方へ左に歩く。",
                    camera="<Subject 2>が歩いている間、カメラをゆっくり左へパンする。",
                ),
            ],
        ),
        required_by_shot=(
            (
                concept("first person raises right hand", "right hand", "right-hand"),
                concept("slow hand raise", "slowly", "slow"),
                fixed_camera(),
            ),
            (
                concept("second person stands up", "stands up", "rises", "gets up", "stands from"),
                concept("second person moves left", "walks left", "moves left", "to the left"),
                concept("moves toward first person", "toward", "towards", "approaches", "moves closer"),
                concept("slow left pan", "slowly pans left", "slow pan to the left", "slow pan left", "pans slowly to the left"),
                concept("camera follows action timing", "while", "during", "as"),
            ),
        ),
        forbidden_by_shot=(
            (concept("future stand/walk leaked into Shot 1", "stands up", "gets up", "walks left", "moves left"),),
            (),
        ),
    ),
]


def _contains_flexible_phrase(text: str, phrase: str) -> bool:
    lowered = text.casefold()
    phrase_lowered = phrase.casefold()
    if phrase_lowered in lowered:
        return True
    words = re.findall(r"[a-z0-9]+", phrase_lowered)
    if len(words) < 2:
        return False
    # Permit at most two short modifier words between sentinel words.
    gap = r"\b(?:\W+\w+){0,2}\W+\b"
    pattern = r"\b" + gap.join(re.escape(word) for word in words) + r"\b"
    return re.search(pattern, lowered) is not None


def _matches_concept(text: str, item: RequiredConcept) -> bool:
    return any(_contains_flexible_phrase(text, alt) for alt in item.alternatives) or any(
        re.search(pattern, text, flags=re.IGNORECASE) is not None
        for pattern in item.patterns
    )


def _validate_sentinels(case: Case, inputs: ComposerInput, output: ComposerOutput) -> list[str]:
    errors: list[str] = []

    try:
        validate_composer_output(inputs, output)
    except Exception as exc:
        errors.append(f"production validation: {exc}")

    all_output_text = "\n".join(
        [
            output.summary_overview,
            output.style_description,
            *(item.appearance_en for item in output.subject_appearances),
            *(shot.description for shot in output.shots),
        ]
    )

    leaked_shot_labels = sorted(
        set(re.findall(r"\[Shot [1-9][0-9]*\]", all_output_text, flags=re.IGNORECASE)),
        key=str.casefold,
    )
    if leaked_shot_labels:
        errors.append(
            "emitted renderer-owned shot labels: " + ", ".join(leaked_shot_labels)
        )

    leaked_timestamps = sorted(
        set(re.findall(r"\bAt\s+\d{2}:\d{2}(?:\.\d+)?\b", all_output_text, flags=re.IGNORECASE)),
        key=str.casefold,
    )
    if leaked_timestamps:
        errors.append(
            "emitted renderer-owned shot timestamps: " + ", ".join(leaked_timestamps)
        )

    bare_subject_labels = sorted(
        set(re.findall(r"(?<!<)\bSubject [1-9][0-9]*\b(?!>)", all_output_text, flags=re.IGNORECASE)),
        key=str.casefold,
    )
    if bare_subject_labels:
        errors.append(
            "bare subject labels must use canonical <Subject N> syntax: "
            + ", ".join(bare_subject_labels)
        )

    known_labels = {s.label for s in inputs.subjects}
    emitted_labels = set(re.findall(r"<Subject [1-9][0-9]*>", all_output_text))
    if inputs.mode == "ref2va":
        unknown = emitted_labels - known_labels
        if unknown:
            errors.append("invented unknown subject labels: " + ", ".join(sorted(unknown)))
    elif emitted_labels:
        errors.append("I2VA leaked internal aliases: " + ", ".join(sorted(emitted_labels)))

    for index, shot in enumerate(output.shots, start=1):
        text = shot.description
        lowered = text.casefold()
        if "detailed_description:" in lowered:
            errors.append(f"Shot {index} emitted renderer-owned detailed_description syntax")
        if "summary:" in lowered:
            errors.append(f"Shot {index} emitted renderer-owned summary syntax")
        if index <= len(case.required_by_shot):
            for required in case.required_by_shot[index - 1]:
                if not _matches_concept(text, required):
                    errors.append(
                        f"Shot {index} lost required semantic concept: {required.name}"
                    )
        if index <= len(case.forbidden_by_shot):
            for forbidden in case.forbidden_by_shot[index - 1]:
                if _matches_concept(text, forbidden):
                    errors.append(
                        f"Shot {index} contains forbidden cross-shot concept: {forbidden.name}"
                    )

    if output.shots and output.shots[0].description.lstrip().casefold().startswith(
        ("the shot cuts", "the camera cuts", "the shot transitions", "the camera transitions", "cut to")
    ):
        errors.append("Shot 1 incorrectly begins with a cut/transition")

    return errors


def _model_label(model_name: str) -> str:
    stem = Path(model_name).stem
    match = re.search(r"(?i)(IQ\d[^._-]*|Q\d(?:_[A-Z0-9]+)*)", stem)
    return match.group(1) if match else stem


class ComfyApiError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, server: str, *, timeout: float, poll_interval: float) -> None:
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.client_id = str(uuid.uuid4())

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = self.server + path
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 30.0)) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ComfyApiError(f"{method} {path} -> HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ComfyApiError(f"Could not reach ComfyUI at {self.server}: {exc}") from exc
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise ComfyApiError(f"{method} {path} returned non-JSON: {body[:1000]}") from exc

    def upload_image(self, path: Path, *, subfolder: str = "h3_scribe_quality") -> str:
        if not path.is_file():
            raise ComfyApiError(f"Quality fixture not found: {path}")
        boundary = "----h3scribe-" + uuid.uuid4().hex
        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
                "Content-Type: image/png\r\n\r\n"
            ).encode("utf-8")
            + path.read_bytes()
            + b"\r\n"
        )
        field("type", "input")
        field("subfolder", subfolder)
        field("overwrite", "true")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        request = urllib.request.Request(
            self.server + "/upload/image",
            data=b"".join(parts),
            headers={
                "Accept": "application/json",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 60.0)) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ComfyApiError(f"POST /upload/image -> HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise ComfyApiError(f"Could not upload fixture to ComfyUI at {self.server}: {exc}") from exc
        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ComfyApiError(f"POST /upload/image returned non-JSON: {body[:1000]}") from exc
        if not isinstance(result, dict) or not result.get("name"):
            raise ComfyApiError(f"Invalid /upload/image response: {result!r}")
        uploaded_subfolder = str(result.get("subfolder") or "").strip("/\\")
        name = str(result["name"])
        return f"{uploaded_subfolder}/{name}" if uploaded_subfolder else name

    def object_info(self, node_class: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(node_class, safe="")
        data = self._request("GET", f"/object_info/{encoded}")
        if not isinstance(data, dict) or node_class not in data:
            raise ComfyApiError(
                f"ComfyUI does not expose node {node_class!r}. "
                "Check that H3 Scribe and ComfyUI_Simple_Qwen3-VL-gguf are loaded."
            )
        info = data[node_class]
        if not isinstance(info, dict):
            raise ComfyApiError(f"Invalid object_info for {node_class!r}: {info!r}")
        return info

    def system_stats(self) -> dict[str, Any] | None:
        try:
            data = self._request("GET", "/system_stats")
            return data if isinstance(data, dict) else None
        except ComfyApiError:
            return None

    def queue(self, graph: dict[str, Any]) -> str:
        data = self._request(
            "POST",
            "/prompt",
            {"prompt": graph, "client_id": self.client_id},
        )
        if not isinstance(data, dict):
            raise ComfyApiError(f"Invalid /prompt response: {data!r}")
        if data.get("error") or data.get("node_errors"):
            raise ComfyApiError(
                "ComfyUI rejected prompt: "
                + json.dumps(data, ensure_ascii=False, indent=2)[:8000]
            )
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyApiError(f"/prompt did not return prompt_id: {data!r}")
        return prompt_id

    def wait_history(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        encoded = urllib.parse.quote(prompt_id, safe="")
        while True:
            data = self._request("GET", f"/history/{encoded}")
            if isinstance(data, dict):
                entry = data.get(prompt_id)
                if isinstance(entry, dict):
                    return entry
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out after {self.timeout:.0f}s waiting for ComfyUI prompt {prompt_id}"
                )
            time.sleep(self.poll_interval)

    def run(self, graph: dict[str, Any]) -> tuple[str, dict[str, Any], float]:
        started = time.perf_counter()
        prompt_id = self.queue(graph)
        history = self.wait_history(prompt_id)
        return prompt_id, history, time.perf_counter() - started


def _required_defaults(info: dict[str, Any], *, overrides: set[str] | None = None) -> dict[str, Any]:
    overrides = overrides or set()
    input_info = info.get("input") or {}
    required = input_info.get("required") or {}
    if not isinstance(required, dict):
        raise ComfyApiError(f"Unexpected object_info required schema: {required!r}")

    values: dict[str, Any] = {}
    for name, spec in required.items():
        if name in overrides:
            continue
        if not isinstance(spec, (list, tuple)) or not spec:
            raise ComfyApiError(f"Unexpected schema for required input {name!r}: {spec!r}")
        input_type = spec[0]
        meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        if "default" in meta:
            values[name] = meta["default"]
        elif isinstance(input_type, list) and input_type:
            values[name] = input_type[0]
        elif input_type == "STRING":
            values[name] = ""
        else:
            raise ComfyApiError(
                f"Required input {name!r} has no discoverable default; "
                "update the quality graph builder for this upstream schema."
            )
    return values


def _combo_options(info: dict[str, Any], input_name: str) -> tuple[list[Any], Any]:
    required = ((info.get("input") or {}).get("required") or {})
    spec = required.get(input_name)
    if not isinstance(spec, (list, tuple)) or not spec:
        return [], None
    options = list(spec[0]) if isinstance(spec[0], list) else []
    meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    default = meta.get("default", options[0] if options else None)
    return options, default


def _select_qwen_mode(simple_info: dict[str, Any], requested: str) -> str:
    options, default = _combo_options(simple_info, "mode")
    if requested != "auto":
        if options and requested not in options:
            raise ComfyApiError(
                f"Simple Qwen mode {requested!r} is not supported by this installation: {options}"
            )
        return requested
    # The bundled H3 workflows are intended to reuse the Qwen model across Analyze/Compose.
    # Prefer Simple Qwen's native keep_vram mode when the installed version exposes it.
    if "keep_vram" in options:
        return "keep_vram"
    if isinstance(default, str) and default:
        return default
    if options:
        return str(options[0])
    raise ComfyApiError("Could not determine Simple Qwen execution mode from /object_info")


def _validate_model_selection(selector_info: dict[str, Any], model: str, mmproj: str) -> None:
    required = ((selector_info.get("input") or {}).get("required") or {})
    for name, value in (("model", model), ("mmproj", mmproj)):
        spec = required.get(name)
        options = list(spec[0]) if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list) else []
        if options and value not in options:
            basenames = {Path(str(option)).name: option for option in options}
            if value in basenames:
                continue
            preview = ", ".join(map(str, options[:8]))
            raise ComfyApiError(
                f"{name} {value!r} is not in H3 Scribe's Comfy model selector. "
                f"Available values start with: {preview}"
            )


def _selector_value(selector_info: dict[str, Any], input_name: str, requested: str) -> str:
    required = ((selector_info.get("input") or {}).get("required") or {})
    spec = required.get(input_name)
    options = list(spec[0]) if isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list) else []
    if requested in options:
        return requested
    for option in options:
        if Path(str(option)).name == requested:
            return str(option)
    return requested


def _history_status_errors(history: dict[str, Any]) -> list[str]:
    status = history.get("status")
    errors: list[str] = []
    if isinstance(status, dict):
        status_str = status.get("status_str")
        completed = status.get("completed")
        if status_str not in (None, "success") or completed is False:
            errors.append(f"Comfy status: {status_str or 'incomplete'}")
        messages = status.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, (list, tuple)) or len(message) < 2:
                    continue
                kind, payload = message[0], message[1]
                if kind in {"execution_error", "execution_interrupted"}:
                    errors.append(json.dumps(payload, ensure_ascii=False))
    return errors


def _history_ui_value(history: dict[str, Any], node_id: str, key: str) -> str:
    outputs = history.get("outputs")
    if not isinstance(outputs, dict):
        return ""
    node = outputs.get(node_id)
    if not isinstance(node, dict):
        return ""
    value = node.get(key)
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def _authoring_json(case: Case) -> str:
    return json.dumps(case.authoring.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))


def _analyze_has_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _validate_analyze_payload(
    case: AnalyzeCase,
    payload: InitialPicturePayload | CastPicturePayload,
) -> list[str]:
    errors: list[str] = []

    if case.kind == "initial":
        if not isinstance(payload, InitialPicturePayload):
            return ["Initial case did not parse as InitialPicturePayload"]
        appearances = [item.appearance_ja for item in payload.subjects]
    else:
        if not isinstance(payload, CastPicturePayload):
            return ["Cast case did not parse as CastPicturePayload"]
        appearances = [payload.appearance_ja]

    if len(appearances) != case.expected_subjects:
        errors.append(f"expected exactly {case.expected_subjects} people, got {len(appearances)}")

    for index, required_concepts in enumerate(case.subject_concepts):
        if index >= len(appearances):
            continue
        appearance = appearances[index]
        for required in required_concepts:
            if not _matches_concept(appearance, required):
                errors.append(f"subject_{index} {required.name} missing")
        if "subject_" in appearance.casefold():
            errors.append(f"subject_{index} Appearance leaked a local subject token")
        for forbidden in case.forbidden_appearance_terms:
            if forbidden.casefold() in appearance.casefold():
                errors.append(
                    f"subject_{index} non-Appearance state/scene leaked into appearance_ja: {forbidden}"
                )

    if case.kind == "initial":
        assert isinstance(payload, InitialPicturePayload)
        if case.require_initial_subject_refs:
            for index in range(case.expected_subjects):
                if f"subject_{index}" not in payload.initial_ja:
                    errors.append(f"Initial description lost subject_{index}")
        if case.require_style and not payload.style_ja.strip():
            errors.append("Initial style_ja unexpectedly empty")

    return errors


def _validate_analyzed_authoring(
    case: AnalyzeCase,
    authoring: AuthoringInput,
    payload: InitialPicturePayload | CastPicturePayload,
) -> list[str]:
    errors: list[str] = []
    if authoring.mode != "ref2va":
        errors.append(f"canonicalized mode changed to {authoring.mode!r}")
    if authoring.reference_image_count != 1:
        errors.append(
            f"canonicalized reference_image_count should be 1, got {authoring.reference_image_count}"
        )

    if case.kind == "initial":
        assert isinstance(payload, InitialPicturePayload)
        if authoring.initial_picture_number != 1:
            errors.append("Initial provenance lost Picture 1 as initial_picture_number")
        if len(authoring.subjects) != len(payload.subjects):
            errors.append("Initial canonicalization changed subject count")
        expected = [
            (f"<Subject {index}>", 1, "initial")
            for index in range(1, len(payload.subjects) + 1)
        ]
        actual = [
            (item.label, item.picture_number, item.source_role)
            for item in authoring.subjects
        ]
        if actual != expected:
            errors.append(f"Initial provenance mismatch: {actual!r} != {expected!r}")
        for index, draft in enumerate(payload.subjects):
            if index < len(authoring.subjects) and authoring.subjects[index].appearance_ja != draft.appearance_ja:
                errors.append(f"Initial Subject {index + 1} Appearance changed during canonicalization")
        if "subject_" in authoring.initial_ja:
            errors.append("Initial canonicalization leaked local subject_N aliases")
        for index in range(1, len(payload.subjects) + 1):
            if f"<Subject {index}>" not in authoring.initial_ja:
                errors.append(f"Initial canonicalization lost <Subject {index}> in initial_ja")
        if authoring.style_ja != payload.style_ja:
            errors.append("Initial style changed during canonicalization")
    else:
        assert isinstance(payload, CastPicturePayload)
        if authoring.initial_picture_number is not None:
            errors.append("Cast-only Ref2VA incorrectly gained an Initial picture")
        expected = [("<Subject 1>", 1, "cast")]
        actual = [
            (item.label, item.picture_number, item.source_role)
            for item in authoring.subjects
        ]
        if actual != expected:
            errors.append(f"Cast provenance mismatch: {actual!r} != {expected!r}")
        if authoring.subjects and authoring.subjects[0].appearance_ja != payload.appearance_ja:
            errors.append("Cast Appearance changed during canonicalization")
        if authoring.initial_ja:
            errors.append("Cast-only canonicalization unexpectedly produced initial_ja")
        if authoring.style_ja:
            errors.append("Cast-only canonicalization unexpectedly produced style_ja")
    return errors


def _load_quality_workflow(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ComfyApiError(f"Quality workflow not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ComfyApiError(f"Quality workflow is invalid JSON: {path}: {exc}") from exc
    if not isinstance(workflow, dict):
        raise ComfyApiError(f"Quality workflow root must be an object: {path}")
    meta = ((workflow.get("extra") or {}).get("h3_quality") or {})
    required_markers = {
        "raw_output_node",
        "final_output_node",
        "model_selector_node",
        "authoring_node",
        "validate_render_node",
        "simple_qwen_node",
    }
    missing = required_markers - set(meta)
    if missing:
        raise ComfyApiError(
            f"Quality workflow is missing h3_quality markers: {', '.join(sorted(missing))}"
        )
    _validate_workflow_links(workflow)
    nodes = _workflow_nodes(workflow)
    expected_marker_types = {
        "raw_output_node": "H3Scribe_TextEditor",
        "final_output_node": "H3Scribe_TextEditor",
        "model_selector_node": "H3Scribe_QwenModelSelector",
        "authoring_node": "H3Scribe_AuthoringEditor",
        "validate_render_node": "H3Scribe_ValidateAndRender",
        "simple_qwen_node": "SimpleQwenVLggufV2",
    }
    for marker, expected_type in expected_marker_types.items():
        node_id = str(meta[marker])
        node = nodes.get(node_id)
        if node is None:
            raise ComfyApiError(f"h3_quality.{marker} points to missing node {node_id!r}")
        actual_type = str(node.get("type", ""))
        if actual_type != expected_type:
            raise ComfyApiError(
                f"h3_quality.{marker} points to {actual_type!r}; expected {expected_type!r}"
            )
    if str(meta["raw_output_node"]) == str(meta["final_output_node"]):
        raise ComfyApiError("Raw and final quality output markers must be different nodes")
    return workflow, meta


def _load_analyze_workflow(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ComfyApiError(f"Analyze quality workflow not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ComfyApiError(f"Analyze quality workflow is invalid JSON: {path}: {exc}") from exc
    if not isinstance(workflow, dict):
        raise ComfyApiError(f"Analyze quality workflow root must be an object: {path}")
    meta = ((workflow.get("extra") or {}).get("h3_quality_analyze") or {})
    if not isinstance(meta, dict):
        raise ComfyApiError("Analyze workflow extra.h3_quality_analyze must be an object")
    if "model_selector_node" not in meta:
        raise ComfyApiError("Analyze workflow is missing model_selector_node marker")
    for kind in ("initial", "cast"):
        branch = meta.get(kind)
        if not isinstance(branch, dict):
            raise ComfyApiError(f"Analyze workflow is missing {kind!r} branch markers")
        missing = {
            "image_node",
            "simple_qwen_node",
            "raw_output_node",
            "canonicalize_node",
            "authoring_output_node",
        } - set(branch)
        if missing:
            raise ComfyApiError(
                f"Analyze {kind} branch is missing markers: {', '.join(sorted(missing))}"
            )
    _validate_workflow_links(workflow)
    nodes = _workflow_nodes(workflow)
    expected = {
        str(meta["model_selector_node"]): "H3Scribe_QwenModelSelector",
    }
    for kind in ("initial", "cast"):
        branch = meta[kind]
        expected.update(
            {
                str(branch["image_node"]): "LoadImage",
                str(branch["simple_qwen_node"]): "SimpleQwenVLggufV2",
                str(branch["raw_output_node"]): "H3Scribe_TextEditor",
                str(branch["canonicalize_node"]): "H3Scribe_CanonicalizeReferences",
                str(branch["authoring_output_node"]): "H3Scribe_AuthoringEditor",
            }
        )
    for node_id, expected_type in expected.items():
        node = nodes.get(node_id)
        if node is None:
            raise ComfyApiError(f"Analyze quality marker points to missing node {node_id!r}")
        actual = str(node.get("type", ""))
        if actual != expected_type:
            raise ComfyApiError(
                f"Analyze quality marker node {node_id} points to {actual!r}; expected {expected_type!r}"
            )
    return workflow, meta


def _workflow_nodes(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise ComfyApiError("Quality workflow has no nodes array")
    result: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or "id" not in node or "type" not in node:
            raise ComfyApiError(f"Invalid workflow node: {node!r}")
        result[str(node["id"])] = node
    return result


def _workflow_links(workflow: dict[str, Any]) -> dict[int, tuple[str, int, str, int, Any]]:
    result: dict[int, tuple[str, int, str, int, Any]] = {}
    for link in workflow.get("links") or []:
        if isinstance(link, list) and len(link) >= 6:
            link_id, origin_id, origin_slot, target_id, target_slot, link_type = link[:6]
        elif isinstance(link, dict):
            link_id = link.get("id")
            origin_id = link.get("origin_id")
            origin_slot = link.get("origin_slot")
            target_id = link.get("target_id")
            target_slot = link.get("target_slot")
            link_type = link.get("type")
        else:
            raise ComfyApiError(f"Invalid workflow link: {link!r}")
        result[int(link_id)] = (
            str(origin_id), int(origin_slot), str(target_id), int(target_slot), link_type
        )
    return result


def _validate_workflow_links(workflow: dict[str, Any]) -> None:
    nodes = _workflow_nodes(workflow)
    links = _workflow_links(workflow)
    for link_id, (origin_id, _origin_slot, target_id, target_slot, _type) in links.items():
        if origin_id not in nodes or target_id not in nodes:
            raise ComfyApiError(f"Workflow link {link_id} references a missing node")
        target_inputs = nodes[target_id].get("inputs") or []
        if target_slot >= len(target_inputs):
            raise ComfyApiError(f"Workflow link {link_id} target slot is out of range")
        if target_inputs[target_slot].get("link") != link_id:
            raise ComfyApiError(
                f"Workflow link {link_id} disagrees with target node {target_id} input {target_slot}"
            )


def _workflow_closure(
    workflow: dict[str, Any],
    output_node_id: str,
    literal_overrides: dict[tuple[str, str], Any],
) -> set[str]:
    nodes = _workflow_nodes(workflow)
    links = _workflow_links(workflow)
    if output_node_id not in nodes:
        raise ComfyApiError(f"Quality output node {output_node_id!r} does not exist")
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        node = nodes[node_id]
        for item in node.get("inputs") or []:
            name = str(item.get("name", ""))
            if (node_id, name) in literal_overrides:
                continue
            link_id = item.get("link")
            if link_id is None:
                continue
            try:
                origin_id = links[int(link_id)][0]
            except KeyError as exc:
                raise ComfyApiError(
                    f"Node {node_id} input {name!r} references missing link {link_id}"
                ) from exc
            visit(origin_id)

    visit(output_node_id)
    return visited


def _workflow_to_api(
    *,
    client: ComfyClient,
    workflow: dict[str, Any],
    output_node_id: str,
    literal_overrides: dict[tuple[str, str], Any],
    info_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Convert one saved, human-openable quality workflow to an API prompt.

    The runner only selects an output closure and replaces test data. It does
    not invent a separate node topology in Python.
    """
    nodes = _workflow_nodes(workflow)
    links = _workflow_links(workflow)
    closure = _workflow_closure(workflow, output_node_id, literal_overrides)
    graph: dict[str, Any] = {}

    for node_id in sorted(closure, key=lambda value: int(value) if value.isdigit() else value):
        node = nodes[node_id]
        node_class = str(node["type"])
        if node_class == "MarkdownNote":
            continue
        info = info_cache.get(node_class)
        if info is None:
            info = client.object_info(node_class)
            info_cache[node_class] = info
        inputs = _required_defaults(info)

        for item in node.get("inputs") or []:
            name = str(item.get("name", ""))
            key = (node_id, name)
            if key in literal_overrides:
                inputs[name] = literal_overrides[key]
                continue
            link_id = item.get("link")
            if link_id is not None:
                origin_id, origin_slot, _target_id, _target_slot, _type = links[int(link_id)]
                if origin_id in closure:
                    inputs[name] = [origin_id, origin_slot]

        graph[node_id] = {"class_type": node_class, "inputs": inputs}
    return graph




def _workflow_static_overrides(meta: dict[str, Any]) -> dict[tuple[str, str], Any]:
    result: dict[tuple[str, str], Any] = {}
    raw = meta.get("input_overrides") or {}
    if not isinstance(raw, dict):
        raise ComfyApiError("quality workflow input_overrides must be an object")
    for node_id, values in raw.items():
        if not isinstance(values, dict):
            raise ComfyApiError(f"input_overrides[{node_id!r}] must be an object")
        for name, value in values.items():
            result[(str(node_id), str(name))] = value
    return result

def _inference_graph_from_workflow(
    *,
    client: ComfyClient,
    workflow: dict[str, Any],
    meta: dict[str, Any],
    case: Case,
    model: str,
    mmproj: str,
    qwen_mode: str,
    info_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    selector = str(meta["model_selector_node"])
    authoring = str(meta["authoring_node"])
    simple = str(meta["simple_qwen_node"])
    overrides = _workflow_static_overrides(meta)
    overrides.update({
        (selector, "model"): model,
        (selector, "mmproj"): mmproj,
        (authoring, "authoring_json"): _authoring_json(case),
        (authoring, "source_snapshot"): "",
        (simple, "mode"): qwen_mode,
        (simple, "unload_all_models"): False,
    })
    return _workflow_to_api(
        client=client,
        workflow=workflow,
        output_node_id=str(meta["raw_output_node"]),
        literal_overrides=overrides,
        info_cache=info_cache,
    )


def _render_graph_from_workflow(
    *,
    client: ComfyClient,
    workflow: dict[str, Any],
    meta: dict[str, Any],
    case: Case,
    raw_qwen_output: str,
    info_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    validate = str(meta["validate_render_node"])
    # Replacing these two linked inputs deliberately cuts the expensive Qwen
    # branch out of the selected final-output closure. The exact raw text from
    # stage 1 is then passed through the production Validate & Render node.
    overrides = _workflow_static_overrides(meta)
    overrides.update({
        (validate, "authoring_json"): _authoring_json(case),
        (validate, "composer_json"): raw_qwen_output,
    })
    return _workflow_to_api(
        client=client,
        workflow=workflow,
        output_node_id=str(meta["final_output_node"]),
        literal_overrides=overrides,
        info_cache=info_cache,
    )


def _analyze_inference_graph_from_workflow(
    *,
    client: ComfyClient,
    workflow: dict[str, Any],
    meta: dict[str, Any],
    case: AnalyzeCase,
    uploaded_image: str,
    model: str,
    mmproj: str,
    qwen_mode: str,
    info_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    branch = meta[case.kind]
    selector = str(meta["model_selector_node"])
    image_node = str(branch["image_node"])
    simple = str(branch["simple_qwen_node"])
    overrides = _workflow_static_overrides(meta)
    overrides.update(
        {
            (selector, "model"): model,
            (selector, "mmproj"): mmproj,
            (image_node, "image"): uploaded_image,
            (simple, "mode"): qwen_mode,
            (simple, "unload_all_models"): False,
        }
    )
    return _workflow_to_api(
        client=client,
        workflow=workflow,
        output_node_id=str(branch["raw_output_node"]),
        literal_overrides=overrides,
        info_cache=info_cache,
    )


def _analyze_authoring_graph_from_workflow(
    *,
    client: ComfyClient,
    workflow: dict[str, Any],
    meta: dict[str, Any],
    case: AnalyzeCase,
    raw_qwen_output: str,
    info_cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    branch = meta[case.kind]
    canonicalize = str(branch["canonicalize_node"])
    raw_input = "initial_json" if case.kind == "initial" else "cast_json"
    overrides = _workflow_static_overrides(meta)
    # Replacing the linked raw input cuts LoadImage + Qwen out of this second
    # prompt. The captured Qwen JSON is passed through production canonicalize
    # + Authoring Editor only.
    overrides[(canonicalize, raw_input)] = raw_qwen_output
    return _workflow_to_api(
        client=client,
        workflow=workflow,
        output_node_id=str(branch["authoring_output_node"]),
        literal_overrides=overrides,
        info_cache=info_cache,
    )


class _GpuSampler:
    def __init__(self, gpu_index: int, interval: float = 0.25) -> None:
        self.gpu_index = gpu_index
        self.interval = interval
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.available = shutil.which("nvidia-smi") is not None

    def _query(self) -> float | None:
        if not self.available:
            return None
        try:
            cp = subprocess.run(
                [
                    "nvidia-smi",
                    "-i",
                    str(self.gpu_index),
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
            first = cp.stdout.strip().splitlines()[0].strip()
            return float(first)
        except Exception:
            self.available = False
            return None

    def baseline(self) -> float | None:
        value = self._query()
        if value is not None:
            self.samples.append(value)
        return value

    def start(self) -> None:
        if not self.available:
            return

        def worker() -> None:
            while not self._stop.is_set():
                value = self._query()
                if value is not None:
                    self.samples.append(value)
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        value = self._query()
        if value is not None:
            self.samples.append(value)

    @property
    def peak(self) -> float | None:
        return max(self.samples) if self.samples else None


def _print_case(case_report: dict[str, Any]) -> None:
    print("\n" + "=" * 96)
    print(case_report["name"])
    print("=" * 96)
    print("\nINPUT\n")
    print(json.dumps(case_report["composer_input"], ensure_ascii=False, indent=2))
    print("\nRAW QWEN OUTPUT\n")
    print(case_report.get("raw_qwen_output", "<none>"))
    if case_report.get("parsed_result") is not None:
        print("\nPARSED RESULT\n")
        print(json.dumps(case_report["parsed_result"], ensure_ascii=False, indent=2))
    if case_report.get("final_h3_prompt"):
        print("\nFINAL H3 PROMPT\n")
        print(case_report["final_h3_prompt"])
    print("\nMETRICS\n")
    print(json.dumps(case_report["metrics"], ensure_ascii=False, indent=2))
    print("\nVALIDATION\n")
    if case_report["status"] == "PASS":
        print("PASS")
    else:
        print("FAIL: " + "; ".join(case_report["errors"]))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dry_run(workflow_path: Path = DEFAULT_WORKFLOW) -> int:
    workflow, meta = _load_quality_workflow(workflow_path)
    print(f"Dry-run workflow: {workflow_path}")
    print(f"Workflow SHA256: {_sha256_file(workflow_path)}")
    print(f"Workflow nodes: {len(workflow.get('nodes') or [])}; raw={meta['raw_output_node']}; final={meta['final_output_node']}")
    print("Validating current H3 Scribe Composer contracts for all cases.")
    base_config = {
        "model_path": "/dummy/Q4.gguf",
        "mmproj_path": "/dummy/mmproj.gguf",
        "n_ctx": 8192,
        "chat_handler": "qwen35",
    }
    for case in CASES:
        inputs = composer_input(case.authoring)
        _system, _user, config_json, seed = compose_request(case.authoring, base_config)
        config = json.loads(config_json)
        assert config["temperature"] == 0.0
        assert config["enable_thinking"] is False
        assert config["force_mmproj"] is True
        response_format = config["extra_completion_response_format"]
        assert response_format["type"] == "json_object"
        schema = response_format["schema"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])
        assert "maxLength" not in json.dumps(schema)
        assert "maxLength" not in json.dumps(ComposerOutput.model_json_schema())
        assert schema["properties"]["shots"]["minItems"] == len(inputs.shots)
        assert schema["properties"]["shots"]["maxItems"] == len(inputs.shots)
        expected_appearances = len(inputs.subjects) if inputs.mode == "ref2va" else 0
        assert schema["properties"]["subject_appearances"]["minItems"] == expected_appearances
        assert schema["properties"]["subject_appearances"]["maxItems"] == expected_appearances
        if inputs.style_ja:
            assert schema["properties"]["style_description"]["minLength"] == 1
        else:
            assert schema["properties"]["style_description"]["const"] == ""
        if inputs.mode == "ref2va":
            assert schema["properties"]["summary_overview"]["minLength"] == 1
        else:
            assert schema["properties"]["summary_overview"]["const"] == ""
        assert seed == 0
        print(f"PASS  {case.name}: {len(inputs.shots)} shot(s)")
    print(f"All {len(CASES)} dry-run cases passed current H3 Scribe contracts.")
    return 0


def _print_analyze_case(case_report: dict[str, Any]) -> None:
    print("\n" + "=" * 96)
    print(case_report["name"])
    print("=" * 96)
    print(f"\nFIXTURE\n\n{case_report['fixture']}")
    print("\nRAW QWEN OUTPUT\n")
    print(case_report.get("raw_qwen_output", "<none>"))
    if case_report.get("parsed_result") is not None:
        print("\nPARSED RESULT\n")
        print(json.dumps(case_report["parsed_result"], ensure_ascii=False, indent=2))
    if case_report.get("analyzed_authoring") is not None:
        print("\nANALYZED AUTHORING\n")
        print(json.dumps(case_report["analyzed_authoring"], ensure_ascii=False, indent=2))
    print("\nMETRICS\n")
    print(json.dumps(case_report["metrics"], ensure_ascii=False, indent=2))
    print("\nVALIDATION\n")
    if case_report["status"] == "PASS":
        print("PASS")
    else:
        print("FAIL: " + "; ".join(case_report["errors"]))


def _dry_run_analyze(workflow_path: Path = DEFAULT_ANALYZE_WORKFLOW) -> int:
    workflow, meta = _load_analyze_workflow(workflow_path)
    print(f"Dry-run Analyze workflow: {workflow_path}")
    print(f"Workflow SHA256: {_sha256_file(workflow_path)}")
    print(
        "Workflow nodes: "
        f"{len(workflow.get('nodes') or [])}; "
        f"initial raw={meta['initial']['raw_output_node']}; "
        f"cast raw={meta['cast']['raw_output_node']}"
    )
    base_config = {
        "model_path": "/dummy/Q4.gguf",
        "mmproj_path": "/dummy/mmproj.gguf",
        "n_ctx": 8192,
        "chat_handler": "qwen35",
    }
    for name, builder, output_model in (
        ("Initial", initial_request, InitialPicturePayload),
        ("Cast", cast_request, CastPicturePayload),
    ):
        _system, _user, config_json, seed = builder(base_config)
        config = json.loads(config_json)
        assert config["temperature"] == 0.0
        assert config["enable_thinking"] is False
        assert config["force_mmproj"] is False
        assert config["max_images"] == 1
        response_format = config["extra_completion_response_format"]
        assert response_format["type"] == "json_object"
        schema = response_format["schema"]
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])
        assert "maxLength" not in json.dumps(schema)
        assert "maxLength" not in json.dumps(output_model.model_json_schema())
        assert seed == 0
        print(f"PASS  {name} request contract")
    for case in ANALYZE_CASES:
        if not case.fixture.is_file():
            raise ComfyApiError(f"Analyze fixture missing: {case.fixture}")
        print(
            f"PASS  {case.name}: {case.fixture.name} "
            f"sha256={_sha256_file(case.fixture)[:16]}..."
        )
    print("Analyze dry-run passed current H3 Scribe request/workflow contracts.")
    return 0


def _main_analyze(args: argparse.Namespace, workflow_path: Path) -> int:
    if args.dry_run:
        return _dry_run_analyze(workflow_path)

    selected_names = set(args.cases or [])
    if selected_names:
        unknown = selected_names - {case.name for case in ANALYZE_CASES}
        if unknown:
            raise SystemExit("Unknown Analyze --case: " + ", ".join(sorted(unknown)))
        cases = [case for case in ANALYZE_CASES if case.name in selected_names]
    else:
        cases = ANALYZE_CASES

    workflow, workflow_meta = _load_analyze_workflow(workflow_path)
    client = ComfyClient(args.server, timeout=args.timeout, poll_interval=args.poll_interval)
    info_cache: dict[str, dict[str, Any]] = {}
    nodes = _workflow_nodes(workflow)
    selector_type = str(nodes[str(workflow_meta["model_selector_node"])]["type"])
    selector_info = client.object_info(selector_type)
    info_cache[selector_type] = selector_info
    _validate_model_selection(selector_info, args.model, args.mmproj)

    simple_types = {
        str(nodes[str(workflow_meta[kind]["simple_qwen_node"])]["type"])
        for kind in ("initial", "cast")
    }
    if len(simple_types) != 1:
        raise ComfyApiError(f"Analyze branches use different Simple Qwen node types: {simple_types}")
    simple_type = next(iter(simple_types))
    simple_info = client.object_info(simple_type)
    info_cache[simple_type] = simple_info
    qwen_mode = _select_qwen_mode(simple_info, args.qwen_mode)

    started_at = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "schema_version": 3,
        "suite": "analyze",
        "started_at_utc": started_at.isoformat(),
        "execution_path": (
            "ComfyUI /upload/image -> LoadImage -> H3 Initial/Cast request -> "
            "SimpleQwenVLggufV2 -> H3 Canonicalize References -> H3 Authoring Editor"
        ),
        "server": client.server,
        "workflow": str(workflow_path),
        "workflow_sha256": _sha256_file(workflow_path),
        "model": args.model,
        "quantization": _model_label(args.model),
        "mmproj": args.mmproj,
        "qwen_mode": qwen_mode,
        "system_stats": client.system_stats(),
        "metrics_note": (
            "Inference wall time includes the real image VLM prompt. Canonicalize wall time is the "
            "second cheap Comfy prompt and does not rerun Qwen. nvidia-smi memory is sampled externally."
        ),
        "cases": [],
    }

    failed = 0
    for case in cases:
        branch = workflow_meta[case.kind]
        raw = ""
        parsed: InitialPicturePayload | CastPicturePayload | None = None
        analyzed_authoring: AuthoringInput | None = None
        errors: list[str] = []
        inference_prompt_id: str | None = None
        canonicalize_prompt_id: str | None = None
        inference_seconds: float | None = None
        canonicalize_seconds: float | None = None
        uploaded_image: str | None = None

        gpu = _GpuSampler(args.gpu_index)
        baseline = gpu.baseline()
        gpu.start()
        try:
            uploaded_image = client.upload_image(case.fixture)
            inference_graph = _analyze_inference_graph_from_workflow(
                client=client,
                workflow=workflow,
                meta=workflow_meta,
                case=case,
                uploaded_image=uploaded_image,
                model=_selector_value(selector_info, "model", args.model),
                mmproj=_selector_value(selector_info, "mmproj", args.mmproj),
                qwen_mode=qwen_mode,
                info_cache=info_cache,
            )
            inference_prompt_id, inference_history, inference_seconds = client.run(inference_graph)
            errors.extend(_history_status_errors(inference_history))
            raw = _history_ui_value(
                inference_history,
                str(branch["raw_output_node"]),
                "h3_editor_value",
            )
            if not raw:
                errors.append("Comfy Analyze inference completed without captured Qwen text")
            else:
                payload_type = InitialPicturePayload if case.kind == "initial" else CastPicturePayload
                try:
                    parsed = parse_model_json(raw, payload_type)
                    errors.extend(_validate_analyze_payload(case, parsed))
                except Exception as exc:
                    errors.append(f"parse: {exc}")

                authoring_graph = _analyze_authoring_graph_from_workflow(
                    client=client,
                    workflow=workflow,
                    meta=workflow_meta,
                    case=case,
                    raw_qwen_output=raw,
                    info_cache=info_cache,
                )
                try:
                    (
                        canonicalize_prompt_id,
                        canonicalize_history,
                        canonicalize_seconds,
                    ) = client.run(authoring_graph)
                    errors.extend(_history_status_errors(canonicalize_history))
                    authoring_text = _history_ui_value(
                        canonicalize_history,
                        str(branch["authoring_output_node"]),
                        "h3_editor_value",
                    )
                    if not authoring_text:
                        errors.append("Comfy canonicalization completed without captured Authoring")
                    else:
                        try:
                            analyzed_authoring = parse_model_json(authoring_text, AuthoringInput)
                            if parsed is not None:
                                errors.extend(
                                    _validate_analyzed_authoring(case, analyzed_authoring, parsed)
                                )
                        except Exception as exc:
                            errors.append(f"authoring parse: {exc}")
                except Exception as exc:
                    errors.append(f"canonicalize: {exc}")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            gpu.stop()

        metrics: dict[str, Any] = {
            "inference_wall_seconds": inference_seconds,
            "canonicalize_wall_seconds": canonicalize_seconds,
            "gpu_memory_baseline_mib": baseline,
            "gpu_memory_peak_mib": gpu.peak,
        }
        if baseline is not None and gpu.peak is not None:
            metrics["gpu_memory_peak_delta_mib"] = gpu.peak - baseline

        case_report = {
            "name": case.name,
            "kind": case.kind,
            "status": "PASS" if not errors else "FAIL",
            "errors": errors,
            "fixture": str(case.fixture),
            "fixture_sha256": _sha256_file(case.fixture),
            "uploaded_image": uploaded_image,
            "comfy_prompt_ids": {
                "inference": inference_prompt_id,
                "canonicalize_authoring": canonicalize_prompt_id,
            },
            "raw_qwen_output": raw,
            "parsed_result": parsed.model_dump(mode="json") if parsed is not None else None,
            "analyzed_authoring": (
                analyzed_authoring.model_dump(mode="json")
                if analyzed_authoring is not None
                else None
            ),
            "metrics": metrics,
        }
        report["cases"].append(case_report)
        _print_analyze_case(case_report)
        if errors:
            failed += 1

    finished_at = datetime.now(timezone.utc)
    report["finished_at_utc"] = finished_at.isoformat()
    report["summary"] = {
        "total_cases": len(cases),
        "passed": len(cases) - failed,
        "failed": failed,
        "wall_seconds": (finished_at - started_at).total_seconds(),
    }

    if args.report:
        report_path = Path(args.report).expanduser()
    else:
        stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(args.model).stem)
        report_path = ROOT / "quality" / "results" / f"{safe_model}-analyze-{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print(
        f"SUMMARY: {report['summary']['passed']}/{report['summary']['total_cases']} PASS; "
        f"{failed} FAIL"
    )
    print(f"REPORT: {report_path}")
    return 1 if failed else 0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run H3 Scribe Composer or Analyze quality cases through a running ComfyUI "
            "and its real Simple Qwen node."
        )
    )
    parser.add_argument(
        "--suite",
        choices=("composer", "analyze"),
        default="composer",
        help="Quality suite to run (default: composer).",
    )
    parser.add_argument(
        "--workflow",
        default=None,
        help=(
            "Human-openable ComfyUI quality workflow JSON. Defaults to the saved workflow "
            "for the selected --suite."
        ),
    )
    parser.add_argument(
        "--server",
        default="http://127.0.0.1:8188",
        help="Running ComfyUI server (default: http://127.0.0.1:8188).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Model value/basename visible in H3 Qwen Model Selector.",
    )
    parser.add_argument(
        "--mmproj",
        default=DEFAULT_MMPROJ,
        help="mmproj value/basename visible in H3 Qwen Model Selector.",
    )
    parser.add_argument(
        "--qwen-mode",
        default="auto",
        help="Simple Qwen execution mode. 'auto' prefers keep_vram when available.",
    )
    parser.add_argument("--gpu-index", type=int, default=0, help="GPU index for nvidia-smi sampling.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=1800.0,
        help="Maximum seconds to wait for each Comfy prompt.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.5,
        help="Seconds between /history polls.",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="Run only this exact case name from the selected suite. Repeatable.",
    )
    parser.add_argument(
        "--report",
        help="Output JSON report path. Default: quality/results/<model>-<timestamp>.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the selected suite request/workflow contracts without contacting ComfyUI.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    default_workflow = DEFAULT_ANALYZE_WORKFLOW if args.suite == "analyze" else DEFAULT_WORKFLOW
    workflow_path = Path(args.workflow or default_workflow).expanduser().resolve()
    if args.suite == "analyze":
        return _main_analyze(args, workflow_path)
    if args.dry_run:
        return _dry_run(workflow_path)

    selected_names = set(args.cases or [])
    if selected_names:
        unknown = selected_names - {case.name for case in CASES}
        if unknown:
            raise SystemExit("Unknown --case: " + ", ".join(sorted(unknown)))
        cases = [case for case in CASES if case.name in selected_names]
    else:
        cases = CASES

    workflow, workflow_meta = _load_quality_workflow(workflow_path)
    client = ComfyClient(args.server, timeout=args.timeout, poll_interval=args.poll_interval)
    info_cache: dict[str, dict[str, Any]] = {}
    selector_type = _workflow_nodes(workflow)[str(workflow_meta["model_selector_node"])]["type"]
    simple_type = _workflow_nodes(workflow)[str(workflow_meta["simple_qwen_node"])]["type"]
    selector_info = client.object_info(str(selector_type))
    simple_info = client.object_info(str(simple_type))
    info_cache[str(selector_type)] = selector_info
    info_cache[str(simple_type)] = simple_info
    _validate_model_selection(selector_info, args.model, args.mmproj)
    qwen_mode = _select_qwen_mode(simple_info, args.qwen_mode)

    started_at = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "schema_version": 2,
        "started_at_utc": started_at.isoformat(),
        "execution_path": "ComfyUI /prompt -> H3 Scribe nodes -> SimpleQwenVLggufV2",
        "server": client.server,
        "workflow": str(workflow_path),
        "workflow_sha256": _sha256_file(workflow_path),
        "model": args.model,
        "quantization": _model_label(args.model),
        "mmproj": args.mmproj,
        "qwen_mode": qwen_mode,
        "system_stats": client.system_stats(),
        "metrics_note": (
            "wall time and nvidia-smi memory are measured externally. Token counts/decode tok/s "
            "are not scraped from Simple Qwen internals; use the Comfy/Simple Qwen console for those "
            "until upstream exposes them as node/API outputs."
        ),
        "cases": [],
    }

    failed = 0
    for case in cases:
        inputs = composer_input(case.authoring)
        gpu = _GpuSampler(args.gpu_index)
        baseline = gpu.baseline()
        gpu.start()
        raw = ""
        parsed: ComposerOutput | None = None
        final_prompt = ""
        errors: list[str] = []
        inference_prompt_id: str | None = None
        render_prompt_id: str | None = None
        inference_seconds: float | None = None
        render_seconds: float | None = None
        try:
            inference_graph = _inference_graph_from_workflow(
                client=client,
                workflow=workflow,
                meta=workflow_meta,
                case=case,
                model=_selector_value(selector_info, "model", args.model),
                mmproj=_selector_value(selector_info, "mmproj", args.mmproj),
                qwen_mode=qwen_mode,
                info_cache=info_cache,
            )
            inference_prompt_id, inference_history, inference_seconds = client.run(inference_graph)
            errors.extend(_history_status_errors(inference_history))
            raw = _history_ui_value(inference_history, str(workflow_meta["raw_output_node"]), "h3_editor_value")
            if not raw:
                errors.append("Comfy inference completed without captured Qwen text")
            else:
                try:
                    parsed = parse_model_json(raw, ComposerOutput)
                    errors.extend(_validate_sentinels(case, inputs, parsed))
                except Exception as exc:
                    errors.append(f"parse: {exc}")

                # Run the exact production H3 Validate & Render node in Comfy as a
                # second, cheap prompt. Keeping it separate preserves raw Qwen output
                # even when validation fails.
                render_graph = _render_graph_from_workflow(
                        client=client, workflow=workflow, meta=workflow_meta, case=case,
                        raw_qwen_output=raw, info_cache=info_cache
                    )
                try:
                    render_prompt_id, render_history, render_seconds = client.run(render_graph)
                    errors.extend(_history_status_errors(render_history))
                    final_prompt = _history_ui_value(render_history, str(workflow_meta["final_output_node"]), "h3_editor_value")
                    if not final_prompt:
                        errors.append("Comfy render completed without captured final prompt")
                except Exception as exc:
                    errors.append(f"render: {exc}")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            gpu.stop()

        metrics: dict[str, Any] = {
            "inference_wall_seconds": inference_seconds,
            "render_wall_seconds": render_seconds,
            "gpu_memory_baseline_mib": baseline,
            "gpu_memory_peak_mib": gpu.peak,
        }
        if baseline is not None and gpu.peak is not None:
            metrics["gpu_memory_peak_delta_mib"] = gpu.peak - baseline
        if parsed is not None:
            shot_lengths = [len(item.description) for item in parsed.shots]
            metrics["summary_chars"] = len(parsed.summary_overview)
            metrics["shot_description_chars"] = shot_lengths
            metrics["total_semantic_output_chars"] = (
                len(parsed.summary_overview)
                + len(parsed.style_description)
                + sum(len(item.appearance_en) for item in parsed.subject_appearances)
                + sum(shot_lengths)
            )

        case_report = {
            "name": case.name,
            "status": "PASS" if not errors else "FAIL",
            "errors": errors,
            "authoring": case.authoring.model_dump(mode="json"),
            "composer_input": inputs.model_dump(mode="json"),
            "comfy_prompt_ids": {
                "inference": inference_prompt_id,
                "validate_render": render_prompt_id,
            },
            "raw_qwen_output": raw,
            "parsed_result": parsed.model_dump(mode="json") if parsed is not None else None,
            "final_h3_prompt": final_prompt,
            "metrics": metrics,
        }
        report["cases"].append(case_report)
        _print_case(case_report)
        if errors:
            failed += 1

    finished_at = datetime.now(timezone.utc)
    report["finished_at_utc"] = finished_at.isoformat()
    report["summary"] = {
        "total_cases": len(cases),
        "passed": len(cases) - failed,
        "failed": failed,
        "wall_seconds": (finished_at - started_at).total_seconds(),
    }

    if args.report:
        report_path = Path(args.report).expanduser()
    else:
        stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
        safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(args.model).stem)
        report_path = ROOT / "quality" / "results" / f"{safe_model}-{stamp}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print(
        f"SUMMARY: {report['summary']['passed']}/{report['summary']['total_cases']} PASS; "
        f"{failed} FAIL"
    )
    print(f"REPORT: {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
