"""Narrator: writes the story segments as plain prose, with **bold** on a scene's key moments."""

from __future__ import annotations

import re
from typing import Literal

from ..context import render_npc_actions, render_scene_plan, render_story_context
from ..llm.base import DeltaCallback, LLMClient
from ..models import Story
from ..pacing import PacingReport
from ..prompts import NARRATOR_SYSTEM
from ..schemas import CharacterActions, CriticReport, ScenePlan

Mode = Literal["opening", "advance", "revision", "ending"]

_MODE_TASKS = {
    "opening": (
        "Write the OPENING segment. Establish the place, the mood and the protagonist's situation, "
        "introduce the hook, and end on the first moment that calls for the protagonist to act."
    ),
    "advance": (
        "Write the NEXT segment from the scene plan. Begin with the immediate consequences of the player's "
        "action, then let the world and the other characters respond."
    ),
    "revision": (
        "REWRITE the most recent segment. It has been removed from the record, so continue from the "
        "state shown above. Apply the revision instructions while keeping the player's original action."
    ),
    "ending": (
        "Write the FINAL segment. Resolve the central conflict and the most important open threads, "
        "show the consequences of the protagonist's choices, and close with a resonant final image. "
        "Do not end on a question or a new choice."
    ),
}

MAX_BOLD_SPANS = 3

_LINE_MARKUP = [
    # Line-anchored patterns use [ \t] rather than \s so they never swallow blank lines.
    (re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.M), ""),  # headings
    (re.compile(r"^[ \t]*(-{3,}|\*{3,})[ \t]*$", re.M), ""),  # horizontal rules
    (re.compile(r"^[ \t]*[-*][ \t]+", re.M), ""),  # bullets
]
_UNDERSCORE_BOLD = re.compile(r"(?<!\w)__([^_\n]+?)__(?!\w)")
# One line, no asterisks inside, and (as in Markdown) no whitespace just inside the markers,
# so stray markers such as "** **" can never pair up across ordinary text.
_BOLD = re.compile(r"\*\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?![\s*])([^*\n]+?)(?<![\s*])\*(?![\w*])")  # single * only, never **
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")


def clean_prose(text: str) -> str:
    """Normalise Narrator output to plain prose plus **bold** key moments.

    `**phrase**` is the only markup kept: at most MAX_BOLD_SPANS spans, each on a
    single line. Extra spans are unwrapped, stray or unbalanced markers are
    dropped, and every other kind of markdown is stripped.
    """
    text = text.replace("\x00", "")
    for pattern, repl in _LINE_MARKUP:
        text = pattern.sub(repl, text)
    text = _UNDERSCORE_BOLD.sub(r"**\1**", text)

    kept: list[str] = []

    def protect(match: re.Match) -> str:
        inner = match.group(1).strip()
        if not inner or len(kept) >= MAX_BOLD_SPANS:
            return inner
        kept.append(inner)
        return f"\x00{len(kept) - 1}\x00"

    text = _BOLD.sub(protect, text)  # valid spans are parked behind placeholders
    text = _ITALIC.sub(r"\1", text)
    text = text.replace("**", "")  # whatever is left is stray, unbalanced or spans lines
    text = _PLACEHOLDER.sub(lambda m: f"**{kept[int(m.group(1))]}**", text)
    text = re.sub(r"[ \t]{2,}", " ", text)  # gaps left by removed markers
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class Narrator:
    """Turns the Scene Director's plan and the Character Agent's notes into prose. Runs for every scene."""

    role = "narrator"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def write(
        self,
        story: Story,
        *,
        mode: Mode,
        pacing: PacingReport,
        plan: ScenePlan,
        npc_actions: CharacterActions | None = None,
        player_action: str = "",
        revision_instructions: str = "",
        critique: CriticReport | None = None,
        rejected_draft: str = "",
        on_delta: DeltaCallback | None = None,
    ) -> str:
        parts = [
            render_story_context(story, segments=2),
            f"=== PACING DIRECTIVE ===\n{pacing.render()}",
            f"=== SCENE PLAN (from the Scene Director) ===\n{render_scene_plan(plan)}",
            f"=== CHARACTER NOTES (from the Character Agent) ===\n{render_npc_actions(npc_actions)}",
            f"=== TASK ===\n{_MODE_TASKS[mode]}",
        ]
        if player_action:
            parts.append(f"PLAYER ACTION: {player_action.strip()}")
        if revision_instructions:
            parts.append(f"REVISION INSTRUCTIONS: {revision_instructions.strip()}")
        if critique is not None and rejected_draft:
            problems = (critique.causality_issues + critique.consistency_issues + critique.agency_issues
                        + critique.quality_issues)
            issues = "\n".join(f"- {i}" for i in problems) or "- (see guidance)"
            parts.append(
                "The Narrative Critic sent a previous draft of this scene back:\n"
                f"{issues}\nGuidance: {critique.rewrite_guidance}\n"
                f"Rejected draft:\n{rejected_draft}\n\nWrite a corrected version."
            )
        parts.append(f"MODE: {mode}\nWrite the segment now, in {story.world.language}.")
        raw = self.llm.text(role=self.role, system=NARRATOR_SYSTEM, prompt="\n\n".join(parts), on_delta=on_delta)
        return clean_prose(raw)
