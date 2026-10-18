"""Narrative Critic: reviews a drafted scene for causality, consistency, player agency and quality.

Runs only at key moments or when the Scheduler spots an anomaly in a draft.
A "revise" verdict sends the scene back to the Narrator once.
"""

from __future__ import annotations

from ..context import render_scene_plan, render_story_context
from ..llm.base import LLMClient
from ..models import Story
from ..prompts import NARRATIVE_CRITIC_SYSTEM
from ..schemas import CriticReport, ScenePlan


class NarrativeCritic:
    role = "narrative_critic"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def review(self, story: Story, *, draft: str, plan: ScenePlan, mode: str, player_action: str,
               reason: str) -> CriticReport:
        prompt = (
            f"{render_story_context(story, segments=1)}\n\n"
            f"=== SCENE PLAN ===\n{render_scene_plan(plan)}\n\n"
            f"PLAYER ACTION: {player_action.strip() or '(none)'}\n"
            f"SEGMENT TYPE: {mode}\n"
            f"WHY THIS REVIEW: {reason}\n\n"
            f"=== DRAFT ===\n{draft}\n\n"
            "Review the draft."
        )
        return self.llm.structured(role=self.role, system=NARRATIVE_CRITIC_SYSTEM, prompt=prompt,
                                   schema=CriticReport)
