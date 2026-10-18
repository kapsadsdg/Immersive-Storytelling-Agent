"""Character Agent: plays the important non-player characters in a scene.

Runs only when the Scene Director's plan involves cast members. One call plays
all of them (up to three), each from their own goals, knowledge, memories and
relationship with the protagonist.
"""

from __future__ import annotations

from ..context import render_character_minds, render_scene_plan, render_world
from ..llm.base import LLMClient
from ..models import Story
from ..prompts import CHARACTER_AGENT_SYSTEM
from ..schemas import CharacterActions, ScenePlan


class CharacterAgent:
    role = "character_agent"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def act(self, story: Story, plan: ScenePlan, names: list[str]) -> CharacterActions:
        wanted = {n.lower() for n in names}
        profiles = "\n".join(
            f"- {c.name} ({c.role}): {c.description} Wants: {c.motivation} Secret: {c.secret or '(none)'}"
            for c in story.characters if c.name.lower() in wanted
        )
        prompt = (
            f"=== WORLD ===\n{render_world(story.world)}\n\n"
            f"PROTAGONIST (played by the player, never by you): {story.protagonist.name}\n\n"
            f"=== CHARACTERS YOU PLAY ===\n{profiles}\n\n"
            f"=== WHAT THEY KNOW AND REMEMBER ===\n{render_character_minds(story.state.characters, names)}\n\n"
            f"=== THE SCENE ===\n{render_scene_plan(plan)}\n\n"
            f"Story so far: {story.state.summary or '(just beginning)'}\n\n"
            f"CHARACTERS TO PLAY: {', '.join(names)}\n"
            "For each of them, decide what they want, know, feel, do, say and hide in this scene."
        )
        return self.llm.structured(role=self.role, system=CHARACTER_AGENT_SYSTEM, prompt=prompt,
                                   schema=CharacterActions)
