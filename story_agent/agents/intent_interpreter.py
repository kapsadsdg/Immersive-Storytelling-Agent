"""Intent Interpreter: understands the player's action, dialogue or story-change request,
and decides whether the system advances, clarifies, revises or concludes.

Runs every turn, except for simple instructions the Scheduler can read on its own.
"""

from __future__ import annotations

from ..context import render_story_context
from ..llm.base import LLMClient
from ..models import Story
from ..pacing import PacingReport
from ..prompts import INTENT_INTERPRETER_SYSTEM
from ..schemas import IntentReading


class IntentInterpreter:
    role = "intent_interpreter"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def read(self, story: Story, player_input: str, pacing: PacingReport) -> IntentReading:
        pending = ""
        if story.status == "awaiting_clarification" and story.pending_question:
            pending = (
                f"You previously asked the player: {story.pending_question}\n"
                "The player's input below is their answer. Prefer to act on it now rather than ask again.\n\n"
            )
        offered = "\n".join(f"- {c}" for c in story.choices) or "(none)"
        prompt = (
            f"{render_story_context(story, segments=1)}\n\n"
            f"=== PACING REPORT ===\n{pacing.render()}\n\n"
            f"=== CHOICES THAT WERE SUGGESTED TO THE PLAYER ===\n{offered}\n\n"
            f"{pending}"
            f"PLAYER INPUT: {player_input.strip()}\n\n"
            "Interpret the input and decide the next action."
        )
        return self.llm.structured(role=self.role, system=INTENT_INTERPRETER_SYSTEM, prompt=prompt,
                                   schema=IntentReading)
