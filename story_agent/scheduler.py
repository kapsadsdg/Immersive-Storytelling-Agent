"""Scheduler (deterministic): decides which of the eight agents run on each turn.

Not every turn needs every agent. The Scheduler applies cheap, explainable
rules so that model calls are spent only where they add something:

  agent               runs when
  ------------------  ---------------------------------------------------------
  World Architect     the story is created
  Plot Planner        the story is created, the player redirects the plot, or
                      the Chronicler records a major turn
  Intent Interpreter  the input is free-form; skipped for a picked suggestion
                      or the End command
  Scene Director      every scene
  Character Agent     cast members take part in the planned scene
  Narrator            every scene
  Narrative Critic    key moments (opening, ending, rewrites, turning points,
                      high tension) or when a draft looks anomalous
  Chronicler          after every scene

Every decision is written to a trace (ran or skipped, and why), which is saved
with the turn and shown in both interfaces.
"""

from __future__ import annotations

import re

from .config import AGENT_LABELS
from .models import AgentStep, Story
from .schemas import Chronicle, IntentReading, ScenePlan

END_REQUEST = "I want the story to end now. Please conclude it."  # sent by the End button and /end
MAX_SCENE_CHARACTERS = 3
HIGH_TENSION = 8
_META_LEAK = re.compile(r"\b(the player|as an ai|language model|option [1-4]|suggested choices?)\b", re.I)
_NUMBERING = re.compile(r"^\s*\d+[.)]\s*")

SKIP_REASONS = {
    "world_architect": "Runs only when a story is created.",
    "plot_planner": "No major plot turn this turn.",
    "intent_interpreter": "Not needed this turn.",
    "scene_director": "No scene was written this turn.",
    "character_agent": "No important character took part.",
    "narrator": "No scene was written this turn.",
    "narrative_critic": "Not a key moment, and the draft showed no anomaly.",
    "chronicler": "No scene was written this turn.",
}


class TurnTrace:
    """Collects which agents ran or were skipped during one turn, with reasons."""

    def __init__(self) -> None:
        self.steps: list[AgentStep] = []

    def ran(self, agent: str, reason: str) -> None:
        self.steps.append(AgentStep(agent=agent, ran=True, reason=reason))

    def skipped(self, agent: str, reason: str) -> None:
        self.steps.append(AgentStep(agent=agent, ran=False, reason=reason))

    def complete(self) -> list[AgentStep]:
        """The full trace: every agent appears at least once, skipped ones with a default reason."""
        present = {s.agent for s in self.steps}
        missing = [AgentStep(agent=a, ran=False, reason=SKIP_REASONS[a]) for a in AGENT_LABELS if a not in present]
        return self.steps + missing

    def summary(self) -> dict[str, list[str]]:
        steps = self.complete()
        ran = list(dict.fromkeys(AGENT_LABELS[s.agent] for s in steps if s.ran))
        skipped = [AGENT_LABELS[s.agent] for s in steps if not s.ran and AGENT_LABELS[s.agent] not in ran]
        return {"ran": ran, "skipped": list(dict.fromkeys(skipped))}


def _normalise(text: str) -> str:
    return _NUMBERING.sub("", text).strip().strip(".!?").casefold()


def blank_reading(**fields) -> IntentReading:
    base = dict(intent_type="action", interpreted_intent="", player_action="", action="advance", rationale="",
                clarifying_question="", clarifying_options=[], revision_scope="none", revision_instructions="",
                plot_change_request="")
    base.update(fields)
    return IntentReading(**base)


def fast_intent(story: Story, player_input: str) -> IntentReading | None:
    """Readings for simple instructions, so the Intent Interpreter can be skipped."""
    text = player_input.strip()
    if text == END_REQUEST:
        return blank_reading(intent_type="end_request", interpreted_intent="End the story now.", action="conclude",
                             rationale="The player used the End command.")
    target = _normalise(text)
    for choice in story.choices:
        if target and _normalise(choice) == target:
            return blank_reading(interpreted_intent=choice, player_action=choice,
                                 rationale="The player picked a suggested choice, so no interpretation was needed.")
    return None


def scene_characters(story: Story, plan: ScenePlan) -> list[str]:
    """Cast members the Character Agent should play: named in the plan, known to the story, at most three."""
    cast = {c.name.lower(): c.name for c in story.characters}
    names: list[str] = []
    for raw in plan.involved_characters:
        name = cast.get(raw.strip().lower())
        if name and name not in names:
            names.append(name)
    return names[:MAX_SCENE_CHARACTERS]


def critic_reason(mode: str, plan: ScenePlan, text: str) -> str:
    """Why the Narrative Critic should review this draft, or an empty string if it should not."""
    if mode == "opening":
        return "The opening sets up the whole story."
    if mode == "ending":
        return "The ending is a key moment."
    if mode == "revision":
        return "Rewritten scenes are always checked."
    if plan.pivotal:
        return "The Scene Director marked this scene as a turning point."
    if plan.tension >= HIGH_TENSION:
        return f"High-tension scene ({plan.tension}/10)."
    words = len(text.split())
    if words < 80:
        return f"Anomaly: the draft is unusually short ({words} words)."
    if words > 900:
        return f"Anomaly: the draft is unusually long ({words} words)."
    if _META_LEAK.search(text):
        return "Anomaly: the draft seems to break the fourth wall."
    return ""


def replan_reason(chronicle: Chronicle, ending: bool) -> str:
    """Why the Plot Planner should re-plan after a scene, or an empty string."""
    if not ending and chronicle.major_turn:
        return "The Chronicler recorded a major plot turn that the outline no longer fits."
    return ""
