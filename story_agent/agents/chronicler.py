"""Chronicler: after each scene, records the state of the world, the key events, what each character
knows and remembers, their relationships with the protagonist, and the long-term summary.

Runs after every finished scene. Its output is committed by the State Manager.
"""

from __future__ import annotations

from ..context import render_cast, render_character_minds, render_events, render_outline, render_state, render_world
from ..llm.base import LLMClient
from ..models import Story
from ..prompts import CHRONICLER_SYSTEM
from ..schemas import Chronicle


class Chronicler:
    role = "chronicler"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def record(self, story: Story, segment_text: str, player_action: str, mode: str) -> Chronicle:
        beat = story.outline.current_beat()
        beat_line = f"{beat.id} '{beat.title}': {beat.description}" if beat else "(none - story resolving)"
        action = player_action.strip() or (
            "(story opening)" if mode == "opening" else "(no direct action: the story moves on)")
        prompt = (
            f"=== WORLD BIBLE ===\n{render_world(story.world)}\n\n"
            f"=== CAST ===\n{render_cast(story)}\n\n"
            f"=== PREVIOUS STATE ===\n{render_state(story.state)}\n\n"
            f"=== WHAT EACH CHARACTER KNOWS AND REMEMBERS ===\n{render_character_minds(story.state.characters)}\n\n"
            f"=== OUTLINE ===\n{render_outline(story.outline)}\n\n"
            f"CURRENT BEAT: {beat_line}\n\n"
            f"=== KEY EVENTS SO FAR ===\n{render_events(story.events)}\n\n"
            f"PLAYER ACTION: {action}\n"
            f"SEGMENT TYPE: {mode}\n\n"
            f"=== NEW SCENE ===\n{segment_text}\n\n"
            "Return the complete updated record after this scene."
        )
        return self.llm.structured(role=self.role, system=CHRONICLER_SYSTEM, prompt=prompt, schema=Chronicle)
