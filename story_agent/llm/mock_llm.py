"""Offline, deterministic stand-in for a real model.

It lets the whole agent loop run without an API key: in the test-suite, in CI,
and as a demo of the interface. It reads the same prompts the real agents send
and answers with canned but schema-valid content. Simple keyword rules imitate
the agents' judgment so that every scheduling path can be exercised on purpose:

  input contains            effect
  ------------------------  -----------------------------------------------
  "end the story", ...      Intent Interpreter concludes
  "rewrite", "revise", ...  revise the last scene ("plot", "from now on"... -> the outline)
  fewer than 3 letters      Intent Interpreter asks a clarifying question
  a cast member's name      Scene Director involves them -> Character Agent runs
  "confront"                pivotal, high-tension scene -> Narrative Critic runs
  "betray"                  Chronicler records a major turn -> Plot Planner re-plans

The prose is placeholder text.
"""

from __future__ import annotations

import re
import time

from ..schemas import (
    BeatSpec,
    CharacterActions,
    CharacterSpec,
    CharacterUpdate,
    Chronicle,
    CriticReport,
    EventSpec,
    InitialStateSpec,
    IntentReading,
    LocationSpec,
    NPCAction,
    PlotPlan,
    ScenePlan,
    WorldBlueprint,
)
from .base import DeltaCallback, LLMClient, LLMError, T

_END_WORDS = ("end the story", "story to end", "conclude", "finish the story", "the end")
_REVISE_WORDS = ("revise", "rewrite", "retcon", "undo", "instead", "change what")
_OUTLINE_WORDS = ("outline", "from now on", "plot", "direction")


def _field(prompt: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}:\s*(.*)$", prompt, re.M)
    value = match.group(1).strip() if match else ""
    return "" if value in ("(none)", "(story opening)") else value


def _names(prompt: str, label: str) -> list[str]:
    return [n.strip() for n in _field(prompt, label).split(",") if n.strip()]


class MockLLM(LLMClient):
    name = "mock"

    def __init__(self, delay: float = 0.0, inject_critic_rejection: bool = False):
        self.delay = delay
        self.inject_critic_rejection = inject_critic_rejection
        self.calls: list[tuple[str, str]] = []

    def roles_called(self) -> list[str]:
        return [role for role, _ in self.calls]

    # ------------------------------------------------------------------ structured

    def structured(self, *, role: str, system: str, prompt: str, schema: type[T]) -> T:
        self.calls.append((role, schema.__name__))
        builders = {
            WorldBlueprint: self._world,
            PlotPlan: self._plot,
            IntentReading: self._intent,
            ScenePlan: self._scene,
            CharacterActions: self._characters,
            CriticReport: self._critic,
            Chronicle: self._chronicle,
        }
        if schema not in builders:
            raise LLMError(f"MockLLM cannot produce {schema.__name__}")
        time.sleep(self.delay)
        return builders[schema](prompt)  # type: ignore[return-value]

    @staticmethod
    def _world(prompt: str) -> WorldBlueprint:
        theme = _field(prompt, "THEME") or "a mystery"
        return WorldBlueprint(
            language="English",
            title="The Lantern at Greywater",
            logline=f"A lamplighter is pulled into a story about {theme}.",
            genre="Mystery",
            tone="Moody, hopeful",
            setting=f"A fog-bound harbour town where every story echoes the theme: {theme}.",
            world_rules=[
                "There is no electricity; light comes from oil lamps and fire.",
                "The fog hides anyone who walks in it for more than a minute.",
                "Spoken promises bind the speaker until dawn.",
                "No one can leave the harbour while the bell is silent.",
            ],
            locations=[
                LocationSpec(name="The Lamp Tower", description="A crooked tower above the docks."),
                LocationSpec(name="The Drowned Market", description="Stalls half-sunk in the tide."),
            ],
            themes=["trust", "memory"],
            narrative_style="Second person, present tense, sensory and quiet.",
            protagonist=CharacterSpec(name="Wren", role="protagonist", description="A young lamplighter.",
                                      motivation="Find out why the harbour bell stopped ringing.", secret="",
                                      knowledge=[]),
            characters=[
                CharacterSpec(name="Old Marsh", role="mentor", description="The retired bell-ringer.",
                              motivation="Protect the town's secret.", secret="He silenced the bell himself.",
                              knowledge=["The bell's clapper is hidden under the tower stairs."]),
                CharacterSpec(name="Ilse", role="rival", description="A smuggler with salt-stiff hair.",
                              motivation="Get her ship out of the harbour.", secret="She owes the fog a debt.",
                              knowledge=["A hidden channel runs under the Drowned Market."]),
                CharacterSpec(name="The Warden", role="antagonist", description="A voice in the fog.",
                              motivation="Keep everyone in the harbour forever.", secret="", knowledge=[]),
            ],
            initial_state=InitialStateSpec(
                location="The Lamp Tower", time="Dusk", situation="The bell has not rung for three nights.",
                protagonist_condition="Tired but alert", inventory=["oil lamp", "brass key"],
                open_threads=["Why is the bell silent?"],
            ),
        )

    @staticmethod
    def _plot(prompt: str) -> PlotPlan:
        request = _field(prompt, "PLOT CHANGE REQUEST")
        reason = _field(prompt, "REPLAN REASON")
        if request:
            beats = [
                BeatSpec(act=2, title="A new direction", description=f"The plot turns: {request}", tension_target=6),
                BeatSpec(act=3, title="New resolution", description="The new thread resolves.", tension_target=8),
            ]
        elif reason:
            beats = [
                BeatSpec(act=2, title="Aftermath", description="Everyone reacts to the betrayal.", tension_target=7),
                BeatSpec(act=3, title="Final reckoning", description="The betrayal is answered.", tension_target=9),
            ]
        else:
            beats = [
                BeatSpec(act=1, title="The silent bell", description="Wren notices the bell is silent.", tension_target=3),
                BeatSpec(act=1, title="A stranger's bargain", description="Ilse offers a deal.", tension_target=4),
                BeatSpec(act=2, title="Into the fog", description="Wren follows a lead into the fog.", tension_target=6),
                BeatSpec(act=2, title="The mentor's lie", description="Marsh's secret comes out.", tension_target=8),
                BeatSpec(act=3, title="Ringing the bell", description="Wren confronts the Warden and resolves the mystery.", tension_target=9),
            ]
        return PlotPlan(central_conflict="Wren must break the Warden's hold on the harbour.", beats=beats,
                        target_turns=6, pacing_style="Slow build, sharp climb in act two, quiet resolution.",
                        rationale="A short mystery arc.")

    @staticmethod
    def _intent(prompt: str) -> IntentReading:
        text = _field(prompt, "PLAYER INPUT")
        low = text.lower()
        base = dict(intent_type="action", interpreted_intent=text, player_action="", clarifying_question="",
                    clarifying_options=[], revision_scope="none", revision_instructions="", plot_change_request="")

        if any(w in low for w in _END_WORDS):
            base.update(intent_type="end_request")
            return IntentReading(action="conclude", rationale="The player asked to end the story.", **base)
        if any(w in low for w in _REVISE_WORDS):
            base.update(intent_type="story_change")
            if any(w in low for w in _OUTLINE_WORDS):
                base.update(revision_scope="outline", plot_change_request=text)
                return IntentReading(action="revise", rationale="The player wants a new plot direction.", **base)
            base.update(revision_scope="last_segment", revision_instructions=text)
            return IntentReading(action="revise", rationale="The player wants the last scene changed.", **base)
        if len(text.strip(" ?.!")) < 3:
            base.update(intent_type="unclear", clarifying_question="What do you want Wren to do?",
                        clarifying_options=["Climb the tower", "Go to the market"])
            return IntentReading(action="clarify", rationale="The input was too unclear to act on.", **base)
        base.update(player_action=text)
        return IntentReading(action="advance", rationale="A clear in-story action.", **base)

    @staticmethod
    def _scene(prompt: str) -> ScenePlan:
        mode = _field(prompt, "MODE")
        action = _field(prompt, "PLAYER ACTION")
        cast = _names(prompt, "CAST NAMES")
        involved = ["Old Marsh"] if mode == "opening" else [n for n in cast if n.lower() in action.lower()]
        pivotal = "confront" in action.lower()
        return ScenePlan(
            scene_goal="Move the mystery forward.",
            location="The Lamp Tower",
            outcome=f"The action succeeds: {action}" if action else "The scene sets things up.",
            events=["The fog thickens.", "Something answers the protagonist's move.", "A new question appears."],
            involved_characters=involved,
            pivotal=pivotal,
            tension=9 if pivotal else 5,
            ending_hook="A voice counts slowly in the fog.",
            narrator_notes="Quiet, tense.",
        )

    @staticmethod
    def _characters(prompt: str) -> CharacterActions:
        return CharacterActions(characters=[
            NPCAction(name=n, goal_in_scene="Keep their secret.", knows="More than they say.", feels="Wary",
                      action="Watches the protagonist closely.", says="Careful, the fog listens.", hides="Their secret.")
            for n in _names(prompt, "CHARACTERS TO PLAY")
        ])

    def _critic(self, prompt: str) -> CriticReport:
        if self.inject_critic_rejection:
            self.inject_critic_rejection = False
            return CriticReport(verdict="revise", causality_issues=[],
                                consistency_issues=["The scene mentions an electric torch, but the world has no electricity."],
                                agency_issues=[], quality_issues=[], rewrite_guidance="Use an oil lamp instead.")
        return CriticReport(verdict="pass", causality_issues=[], consistency_issues=[], agency_issues=[],
                            quality_issues=[], rewrite_guidance="")

    @staticmethod
    def _chronicle(prompt: str) -> Chronicle:
        mode = _field(prompt, "SEGMENT TYPE")
        action = _field(prompt, "PLAYER ACTION")
        if action.startswith("(no direct action"):
            action = ""
        ended = mode == "ending"
        return Chronicle(
            location="The Lamp Tower",
            time="Night",
            situation=f"After: {action}" if action else "The story begins.",
            protagonist_condition="Determined",
            inventory=["oil lamp", "brass key"],
            present_characters=["Old Marsh"],
            character_updates=[CharacterUpdate(name="Old Marsh", status="At the tower", disposition="Wary",
                                               learned=[f"Wren chose to: {action}"] if action else [],
                                               memory=f"Wren {action.lower()}" if action else "Wren arrived at dusk.")],
            open_threads=[] if ended else ["Why is the bell silent?"],
            tension=5,
            summary=f"Wren investigates the silent bell. Latest: {action or 'the opening'}.",
            new_key_events=[EventSpec(description=f"Wren acted: {action or 'arrived at the tower'}",
                                      significance="major", consequences="The mystery deepens.")],
            beat_completed=mode in ("advance", "revision"),
            major_turn="betray" in action.lower(),
            suggested_choices=[] if ended else ["Climb the tower", "Question Old Marsh", "Walk into the fog"],
        )

    # ------------------------------------------------------------------ text

    def text(self, *, role: str, system: str, prompt: str, on_delta: DeltaCallback | None = None) -> str:
        self.calls.append((role, "text"))
        mode = _field(prompt, "MODE") or "advance"
        action = _field(prompt, "PLAYER ACTION")
        if mode == "opening":
            body = ("Fog rolls over the harbour as you climb the last steps of the Lamp Tower. "
                    "Below, the great bell hangs **silent** for the third night running, and the gulls have gone "
                    "quiet with it. The lamp in your hand throws a small, trembling circle of light.\n\n"
                    "Old Marsh waits by the railing, his pipe unlit, his coat buttoned wrong as if he dressed in a "
                    "hurry. \"You heard it too,\" he says without turning. \"Nothing. Three nights of nothing.\" "
                    "Somewhere out in the fog a rope creaks, slow and patient, though no wind is blowing.")
        elif mode == "ending":
            body = ("You pull the rope with everything you have left, and **the bell answers at last**. "
                    "The sound rolls over the water and the fog tears open like wet paper, and the harbour "
                    "breathes again.\n\n"
                    "At dawn the promises lift. The ships slip out one by one, their lanterns paling in the new "
                    "light, and you climb the tower one last time to put out the final lamp yourself.")
        else:
            body = (f"You decide: {action or 'to go on'}. **The lamp gutters** as the fog presses closer, "
                    "beading on your sleeves and swallowing the edges of the street until only the next few "
                    "cobbles are real.\n\n"
                    "Somewhere beyond the docks a voice counts slowly, as if it has been waiting for your next "
                    "move. The numbers do not go up. They go down. You feel the brass key warm against your ribs, "
                    "and you wonder, not for the first time tonight, who else in this town is listening.")
        for word in re.split(r"(\s+)", body):
            if word and on_delta:
                on_delta(word)
        return body
