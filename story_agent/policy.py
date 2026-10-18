"""Policy Guard (deterministic): hard rules applied on top of the Intent Interpreter's decision.

Models are good at judging intent and bad at counting or keeping promises
across turns. These rules keep the loop from stalling or running forever:

* the story concludes at its length limit, or once every beat is complete;
* the player is never asked two clarifying questions in a row;
* a revision is only attempted when it is possible (a scene to roll back,
  or a stated new direction for the plot).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Story
from .pacing import PacingReport
from .schemas import IntentReading


@dataclass(frozen=True)
class PolicyOutcome:
    action: str  # advance | clarify | revise | conclude
    scope: str = "none"  # none | outline | last_segment | both
    note: str = ""  # set when the guard overrode the Intent Interpreter


def plot_request(reading: IntentReading) -> str:
    """The new direction the player asked for, to hand to the Plot Planner."""
    return reading.plot_change_request.strip() or reading.interpreted_intent.strip()


def apply_policy(story: Story, r: IntentReading, pacing: PacingReport) -> PolicyOutcome:
    action, scope = r.action, r.revision_scope
    has_request = bool(r.plot_change_request.strip())

    if action == "revise":
        if scope == "none":
            scope = "outline" if has_request else ("last_segment" if r.revision_instructions.strip() else "none")
        if scope in ("last_segment", "both") and (not story.snapshots or not story.segments):
            scope = "outline" if has_request else "none"
        if scope in ("outline", "both") and not plot_request(r):
            scope = "last_segment" if scope == "both" else "none"
        if scope == "none":
            return PolicyOutcome("conclude" if pacing.must_conclude else "advance", "none",
                                 "There was nothing concrete to revise.")
        if scope == "outline" and pacing.turn >= pacing.max_turns:
            return PolicyOutcome("conclude", "none", "The story reached its length limit, so it concludes.")
        return PolicyOutcome("revise", scope)

    if pacing.must_conclude and action != "conclude":
        reason = "Every outline beat is complete" if pacing.beat is None else "The story reached its length limit"
        return PolicyOutcome("conclude", "none", f"{reason}, so the story concludes.")

    if action == "clarify":
        if story.status == "awaiting_clarification":
            return PolicyOutcome("advance", "none",
                                 "A question was already asked, so the story proceeds with the best reading.")
        if not r.clarifying_question.strip():
            return PolicyOutcome("advance", "none")
        return PolicyOutcome("clarify", "none")

    return PolicyOutcome(action, "none")
