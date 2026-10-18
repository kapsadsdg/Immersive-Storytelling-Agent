"""Pacing Controller (deterministic).

The LLM agents are good at prose and judgment but poor at counting. This module
does the counting: how far into the outline we are, how long the current beat
has run, and when the story must wrap up. Its report is fed to the Intent
Interpreter, the Scene Director, the Narrator and the Plot Planner, and its hard
limits are enforced by the Policy Guard.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Beat, Story


@dataclass(frozen=True)
class PacingReport:
    turn: int
    target_turns: int
    max_turns: int
    beats_done: int
    beats_total: int
    beat: Beat | None
    beat_budget: int
    phase: str
    tension_target: int
    should_wrap_beat: bool
    must_conclude: bool

    @property
    def progress(self) -> float:
        return self.beats_done / self.beats_total if self.beats_total else 1.0

    def render(self) -> str:
        if self.beat is None:
            beat_line = "All outline beats are complete. The story should now resolve."
        else:
            beat_line = (
                f"Current beat: {self.beat.id} '{self.beat.title}' - {self.beat.description}\n"
                f"Turns spent on this beat: {self.beat.turns_spent} of a planned {self.beat_budget}."
            )
        lines = [
            f"Story phase: {self.phase}. Turn {self.turn} of about {self.target_turns} (hard limit {self.max_turns}).",
            f"Beats completed: {self.beats_done}/{self.beats_total}.",
            beat_line,
            f"Target tension for the next segment: {self.tension_target}/10.",
        ]
        if self.should_wrap_beat:
            lines.append("This beat has run long: bring it to its turning point in the next segment.")
        if self.must_conclude:
            lines.append("The story has reached its limit and must conclude now.")
        return "\n".join(lines)


def _phase(progress: float, beat: Beat | None) -> str:
    if beat is None:
        return "resolution"
    if beat.act <= 1 and progress < 0.34:
        return "setup"
    if beat.act >= 3 or progress >= 0.75:
        return "climax and resolution"
    return "rising action"


def assess(story: Story) -> PacingReport:
    outline = story.outline
    beat = outline.current_beat()
    total = len(outline.beats)
    done = outline.done_count()
    budget = max(1, round(story.pacing.target_turns / max(total, 1)))
    turn = story.state.turn

    if beat is None:
        tension = 3
    else:
        # Lean the next segment toward the beat's target, moving at most 3 points at a time.
        current = story.state.tension
        tension = max(1, min(10, current + max(-3, min(3, beat.tension_target - current))))

    return PacingReport(
        turn=turn,
        target_turns=story.pacing.target_turns,
        max_turns=story.pacing.max_turns,
        beats_done=done,
        beats_total=total,
        beat=beat,
        beat_budget=budget,
        phase=_phase(done / total if total else 1.0, beat),
        tension_target=tension,
        should_wrap_beat=beat is not None and beat.turns_spent >= budget + 1,
        must_conclude=beat is None or turn >= story.pacing.max_turns,
    )
