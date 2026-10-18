"""StoryEngine: the orchestrator that runs a story from theme to ending.

Eight logical agents do the creative work; five deterministic modules keep it
on track. The engine wires them together, and the Scheduler decides which
agents run on each turn.

    start(theme)
        World Architect -> world, rules, cast, secrets, opening situation
        Plot Planner    -> central conflict, chapters, key beats, pacing
        State Manager   -> builds the Story
        scene(opening)

    play(story, player_input)
        Pacing Controller  measures progress against the outline
        Scheduler          reads simple input itself, or calls the Intent Interpreter
        Policy Guard       enforces hard limits on the decision
        clarify  -> ask one question; nothing else runs
        revise   -> roll back the last scene and/or have the Plot Planner re-plan, then scene()
        advance  -> scene()
        conclude -> scene(ending)

    scene(mode)
        Scene Director   plans what happens          (every scene)
        Character Agent  plays the NPCs involved     (only if cast members take part)
        Narrator         writes the prose, streamed  (every scene)
        Narrative Critic reviews the draft           (key moments or anomalies; may send it back once)
        Chronicler       records state and memories  (every scene)
        Plot Planner     re-plans                    (only after a major turn)

Every turn's dispatch trace (which agents ran or were skipped, and why) is
saved with the turn's DecisionRecord.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import scheduler, state_manager
from .agents import (
    CharacterAgent,
    Chronicler,
    IntentInterpreter,
    NarrativeCritic,
    Narrator,
    PlotPlanner,
    SceneDirector,
    WorldArchitect,
)
from .config import AGENT_LABELS, Settings
from .llm.base import LLMClient
from .models import DecisionRecord, Segment, Story, utc_now
from .pacing import assess
from .policy import PolicyOutcome, apply_policy, plot_request
from .scheduler import TurnTrace
from .schemas import CharacterActions, IntentReading
from .storage import JSONStorage


@dataclass
class EngineEvent:
    kind: str  # status | story | decision | delta | reset | trace
    data: dict[str, Any] = field(default_factory=dict)


EventCallback = Callable[[EngineEvent], None]


@dataclass
class TurnResult:
    action: str  # opening | advance | clarify | revise | conclude | ended
    text: str
    choices: list[str]
    rationale: str = ""
    status: str = "active"
    notes: list[str] = field(default_factory=list)
    agents_ran: list[str] = field(default_factory=list)


_SCENE_MESSAGES = {
    "opening": "Setting the opening scene",
    "advance": "Planning what happens next",
    "revision": "Re-planning the scene you asked to change",
    "ending": "Planning the final scene",
}


class StoryEngine:
    def __init__(self, llm: LLMClient, settings: Settings | None = None, storage: JSONStorage | None = None):
        self.llm = llm
        self.settings = settings or Settings(provider=llm.name)
        self.storage = storage
        self.world_architect = WorldArchitect(llm)
        self.plot_planner = PlotPlanner(llm)
        self.intent_interpreter = IntentInterpreter(llm)
        self.scene_director = SceneDirector(llm)
        self.character_agent = CharacterAgent(llm)
        self.narrator = Narrator(llm)
        self.narrative_critic = NarrativeCritic(llm)
        self.chronicler = Chronicler(llm)

    # ------------------------------------------------------------------ public API

    def start(self, theme: str, on_event: EventCallback | None = None) -> tuple[Story, TurnResult]:
        emit = on_event or (lambda e: None)
        if not theme.strip():
            raise ValueError("Please give the story a theme.")
        trace = TurnTrace()
        language = self.settings.story_language

        self._status(emit, "world_architect", "Building the world, its rules and its people")
        blueprint = self.world_architect.design(theme, language)
        trace.ran("world_architect", "A new story is being created.")

        self._status(emit, "plot_planner", "Planning the chapters and the central conflict")
        plan = self.plot_planner.initial(theme, blueprint)
        trace.ran("plot_planner", "A new story needs a plot.")

        story = state_manager.create_story(theme, blueprint, plan, language)
        self._save(story)
        emit(EngineEvent("story", {"story_id": story.id, "title": story.world.title}))

        trace.skipped("intent_interpreter", "There is no player input yet.")
        notes = self._scene(story, trace, emit, mode="opening")
        story.decisions.append(DecisionRecord(turn=0, user_input="(story start)", action="opening",
                                              rationale="The story was created from the theme.",
                                              trace=trace.complete()))
        return story, self._finish(story, trace, emit, action="opening", rationale="", notes=notes)

    def play(self, story: Story, player_input: str, on_event: EventCallback | None = None) -> TurnResult:
        emit = on_event or (lambda e: None)
        if story.status == "concluded":
            return TurnResult(action="ended", text="This story has ended. Start a new one to play again.",
                              choices=[], status=story.status)
        player_input = player_input.strip() or "I wait and watch what happens."
        trace = TurnTrace()

        # Perceive and decide.
        pacing = assess(story)
        reading = scheduler.fast_intent(story, player_input)
        if reading is not None:
            trace.skipped("intent_interpreter", reading.rationale)
        else:
            self._status(emit, "intent_interpreter", "Reading your move")
            reading = self.intent_interpreter.read(story, player_input, pacing)
            trace.ran("intent_interpreter", "Free-form input needs interpreting.")
        outcome = apply_policy(story, reading, pacing)

        record = DecisionRecord(turn=story.state.turn, user_input=player_input, action=outcome.action,  # type: ignore[arg-type]
                                rationale=reading.rationale, revision_scope=outcome.scope,
                                overridden_by_policy=outcome.note)
        story.decisions.append(record)
        emit(EngineEvent("decision", {"action": outcome.action, "scope": outcome.scope,
                                      "rationale": reading.rationale, "policy_note": outcome.note}))
        notes = [outcome.note] if outcome.note else []

        if outcome.action == "clarify":
            story.status = "awaiting_clarification"
            story.pending_question = reading.clarifying_question.strip()
            if reading.clarifying_options:
                story.choices = [o for o in reading.clarifying_options if o.strip()][:4]
            for agent in ("scene_director", "character_agent", "narrator", "narrative_critic", "chronicler"):
                trace.skipped(agent, "Waiting for the player's answer to a question.")
            record.trace = trace.complete()
            return self._finish(story, trace, emit, action="clarify", rationale=reading.rationale, notes=notes,
                                text=story.pending_question)

        # Act.
        guidance = reading.interpreted_intent
        if story.status == "awaiting_clarification" and story.pending_question:
            guidance = f"The player was asked: {story.pending_question} Their reply means: {reading.interpreted_intent}"
        story.status = "active"
        story.pending_question = ""
        # Only in-story actions reach the scene as the protagonist's action. A request such as
        # "end the story now" is an instruction to the system, not something the protagonist does.
        action_text = reading.player_action.strip() or (player_input if outcome.action == "advance" else "")
        if outcome.action == "conclude" and not action_text:
            guidance = f"The player asked to end the story here. {guidance}".strip()

        if outcome.action == "revise":
            notes += self._revise(story, reading, outcome, player_input, trace, emit)
        else:
            mode = "ending" if outcome.action == "conclude" else "advance"
            notes += self._scene(story, trace, emit, mode=mode, player_action=action_text,
                                 record_input=player_input, guidance=guidance)

        if story.status != "concluded" and story.outline.is_complete():
            notes.append("The final beat is complete. Your next move will bring the story to its close.")
        record.trace = trace.complete()
        return self._finish(story, trace, emit, action=outcome.action, rationale=reading.rationale, notes=notes)

    # ------------------------------------------------------------------ act

    def _revise(self, story: Story, reading: IntentReading, outcome: PolicyOutcome, player_input: str,
                trace: TurnTrace, emit: EventCallback) -> list[str]:
        notes: list[str] = []
        retcon = outcome.scope in ("last_segment", "both")
        original_input = player_input

        if retcon:
            original_input = story.segments[-1].user_input
            story.restore_last_snapshot()
            notes.append("The last scene was rolled back and rewritten.")

        if outcome.scope in ("outline", "both"):
            request = plot_request(reading)
            self._status(emit, "plot_planner", "Re-planning the plot in the new direction")
            plan = self.plot_planner.replan(story, assess(story), reason="The player asked to change the plot.",
                                            request=request)
            trace.ran("plot_planner", "The player asked to change the plot.")
            if state_manager.replace_future_beats(story, plan, request):
                notes.append("The plot outline was revised.")

        if retcon:
            mode = "revision" if story.segments else "opening"  # rolling back the first scene rewrites the opening
            notes += self._scene(story, trace, emit, mode=mode, player_action=original_input,
                                 record_input=original_input,
                                 revision_instructions=reading.revision_instructions.strip() or player_input)
        else:
            notes += self._scene(story, trace, emit, mode="advance", record_input=player_input,
                                 guidance=f"The story's direction has just changed: {plot_request(reading)}. "
                                          "Begin moving toward the new direction.")
        return notes

    def _scene(self, story: Story, trace: TurnTrace, emit: EventCallback, *, mode: str, player_action: str = "",
               record_input: str | None = None, guidance: str = "", revision_instructions: str = "") -> list[str]:
        """Plan, cast, write, review and record one scene, running only the agents it needs."""
        notes: list[str] = []
        story.push_snapshot()
        pacing = assess(story)

        self._status(emit, "scene_director", _SCENE_MESSAGES[mode])
        plan = self.scene_director.plan(story, mode=mode, pacing=pacing, player_action=player_action,
                                        guidance=guidance, revision_instructions=revision_instructions)
        trace.ran("scene_director", "Every scene is planned before it is written.")

        names = scheduler.scene_characters(story, plan)
        npc_actions: CharacterActions | None = None
        if names:
            self._status(emit, "character_agent", f"Giving voice to {', '.join(names)}")
            npc_actions = self.character_agent.act(story, plan, names)
            trace.ran("character_agent", f"{', '.join(names)} {'takes' if len(names) == 1 else 'take'} part.")
        else:
            trace.skipped("character_agent", "No important character takes part in this scene.")

        def stream(delta: str) -> None:
            emit(EngineEvent("delta", {"text": delta}))

        self._status(emit, "narrator", "Writing the scene")
        text = self.narrator.write(story, mode=mode, pacing=pacing, plan=plan, npc_actions=npc_actions,
                                   player_action=player_action, revision_instructions=revision_instructions,
                                   on_delta=stream)
        trace.ran("narrator", "Every scene is written as prose.")

        reason = scheduler.critic_reason(mode, plan, text)
        if reason and self.settings.self_correction:
            self._status(emit, "narrative_critic", "Checking causality, consistency and player agency")
            report = self.narrative_critic.review(story, draft=text, plan=plan, mode=mode,
                                                  player_action=player_action, reason=reason)
            trace.ran("narrative_critic", reason)
            if report.verdict == "revise":
                self._status(emit, "narrator", "Rewriting the scene after the Critic's review")
                emit(EngineEvent("reset", {}))
                text = self.narrator.write(story, mode=mode, pacing=pacing, plan=plan, npc_actions=npc_actions,
                                           player_action=player_action, revision_instructions=revision_instructions,
                                           critique=report, rejected_draft=text, on_delta=stream)
                trace.ran("narrator", "The Narrative Critic asked for a rewrite.")
                notes.append("The Narrative Critic sent the scene back once, and it was rewritten.")
        elif reason:
            trace.skipped("narrative_critic", f"{reason} Self-correction is turned off.")
        else:
            trace.skipped("narrative_critic", "Not a key moment, and the draft showed no anomaly.")

        self._status(emit, "chronicler", "Recording what changed")
        chronicle = self.chronicler.record(story, text, player_action, mode)
        trace.ran("chronicler", "Every finished scene is recorded.")
        ending = mode == "ending"
        state_manager.apply_chronicle(story, chronicle, ending=ending)
        story.segments.append(Segment(turn=story.state.turn, kind=mode, text=text,  # type: ignore[arg-type]
                                      user_input=record_input if record_input is not None else player_action))

        replan = scheduler.replan_reason(chronicle, ending)
        if replan:
            self._status(emit, "plot_planner", "Re-planning after a major turn")
            new_plan = self.plot_planner.replan(story, assess(story), reason=replan)
            trace.ran("plot_planner", replan)
            if state_manager.replace_future_beats(story, new_plan, replan):
                notes.append("A major turn changed the story's course, so the plot was re-planned.")
        return notes

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _status(emit: EventCallback, agent: str, message: str) -> None:
        emit(EngineEvent("status", {"agent": agent, "label": AGENT_LABELS[agent], "message": message}))

    def _finish(self, story: Story, trace: TurnTrace, emit: EventCallback, *, action: str, rationale: str,
                notes: list[str], text: str | None = None) -> TurnResult:
        summary = trace.summary()
        emit(EngineEvent("trace", summary))
        self._save(story)
        return TurnResult(action=action, text=story.segments[-1].text if text is None else text,
                          choices=story.choices, rationale=rationale, status=story.status, notes=notes,
                          agents_ran=summary["ran"])

    def _save(self, story: Story) -> None:
        story.updated_at = utc_now()
        if self.storage is not None:
            self.storage.save(story)
