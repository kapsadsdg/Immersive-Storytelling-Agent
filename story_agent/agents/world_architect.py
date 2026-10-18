"""World Architect: creates the world, its rules, the cast and their secrets, and the opening situation.

Runs once, when a story is created.
"""

from __future__ import annotations

from ..llm.base import LLMClient
from ..prompts import WORLD_ARCHITECT_SYSTEM
from ..schemas import WorldBlueprint


class WorldArchitect:
    role = "world_architect"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def design(self, theme: str, language: str = "English") -> WorldBlueprint:
        if language.lower() == "auto":
            language_line = "STORY LANGUAGE: the same language the theme is written in"
        else:
            language_line = f"STORY LANGUAGE: {language}, whatever language the theme is written in"
        prompt = (
            f"THEME: {theme.strip()}\n{language_line}\n\n"
            "Create the world, its rules, the protagonist the player will play, the supporting cast and the "
            "opening situation."
        )
        return self.llm.structured(role=self.role, system=WORLD_ARCHITECT_SYSTEM, prompt=prompt,
                                   schema=WorldBlueprint)
