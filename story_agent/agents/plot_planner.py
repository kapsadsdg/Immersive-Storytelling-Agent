"""Plot Planner: plans the chapters, the central conflict and the key events.

Runs when a story is created, and again at major turns: when the player
redirects the plot, or when the Chronicler records a turn the outline no
longer fits. Re-plans only replace beats that have not happened yet.
"""

from __future__ import annotations

from ..context import render_blueprint, render_story_context
from ..llm.base import LLMClient
from ..models import Story
from ..pacing import PacingReport
from ..prompts import PLOT_PLANNER_SYSTEM
from ..schemas import PlotPlan, WorldBlueprint


class PlotPlanner:
    role = "plot_planner"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def initial(self, theme: str, bp: WorldBlueprint) -> PlotPlan:
        prompt = (
            f"THEME: {theme.strip()}\n\n=== WORLD AND CAST ===\n{render_blueprint(bp)}\n\n"
            "Plan the whole story: the central conflict, 5-8 beats across three chapters ending in a resolution, "
            "the number of player turns, and the pacing."
        )
        return self.llm.structured(role=self.role, system=PLOT_PLANNER_SYSTEM, prompt=prompt, schema=PlotPlan)

    def replan(self, story: Story, pacing: PacingReport, *, reason: str, request: str = "") -> PlotPlan:
        done = sum(1 for b in story.outline.beats if b.status == "done")
        prompt = (
            f"{render_story_context(story, segments=1)}\n\n"
            f"=== PACING REPORT ===\n{pacing.render()}\n\n"
            f"REPLAN REASON: {reason}\n"
            f"PLOT CHANGE REQUEST: {request.strip() or '(none)'}\n\n"
            f"{done} beat(s) are complete and fixed. Return the central conflict (updated if it has changed) and "
            "only the beats still to come, ending in a resolution."
        )
        return self.llm.structured(role=self.role, system=PLOT_PLANNER_SYSTEM, prompt=prompt, schema=PlotPlan)
