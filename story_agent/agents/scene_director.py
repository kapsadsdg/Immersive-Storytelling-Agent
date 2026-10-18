"""Scene Director: plans the current scene from the player's action and the state of the world.

Runs for every scene. Its plan says what the action achieves, which events
happen, who takes part and where the scene ends; the Narrator writes it.
"""

from __future__ import annotations

from ..context import render_story_context
from ..llm.base import LLMClient
from ..models import Story
from ..pacing import PacingReport
from ..prompts import SCENE_DIRECTOR_SYSTEM
from ..schemas import ScenePlan

_TASKS = {
    "opening": "Plan the OPENING scene: establish the place and the protagonist's situation, introduce the hook, "
               "and end on the first moment that calls for the protagonist to act.",
    "advance": "Plan the NEXT scene, starting from the consequences of the player's action.",
    "revision": "Plan a REPLACEMENT for the most recent scene, which has been removed from the record. Keep the "
                "player's original action and apply the revision instructions.",
    "ending": "Plan the FINAL scene: resolve the central conflict and the most important open threads, and show "
              "the consequences of the protagonist's choices. It must not end on a new choice.",
}


class SceneDirector:
    role = "scene_director"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def plan(self, story: Story, *, mode: str, pacing: PacingReport, player_action: str = "",
             guidance: str = "", revision_instructions: str = "") -> ScenePlan:
        parts = [
            render_story_context(story, segments=1),
            f"=== PACING DIRECTIVE ===\n{pacing.render()}",
            f"=== TASK ===\n{_TASKS[mode]}",
            f"CAST NAMES: {', '.join(c.name for c in story.characters)}",
            f"MODE: {mode}",
            f"PLAYER ACTION: {player_action.strip() or '(none)'}",
        ]
        if guidance:
            parts.append(f"NOTES FROM THE INTENT INTERPRETER: {guidance.strip()}")
        if revision_instructions:
            parts.append(f"REVISION INSTRUCTIONS: {revision_instructions.strip()}")
        return self.llm.structured(role=self.role, system=SCENE_DIRECTOR_SYSTEM, prompt="\n\n".join(parts),
                                   schema=ScenePlan)
