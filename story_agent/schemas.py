"""Structured-output schemas the agents ask the LLM to fill in.

These are deliberately simple (every field required, no dicts, no numeric
constraints) so the same Pydantic classes work with Anthropic structured
outputs, OpenAI strict JSON schema, and JSON-mode fallbacks on local models.
Values are clamped and validated in code after parsing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- shared pieces


class LocationSpec(BaseModel):
    name: str
    description: str


class CharacterSpec(BaseModel):
    name: str
    role: str = Field(description="e.g. protagonist, mentor, rival, antagonist, ally")
    description: str = Field(description="Appearance and personality in 1-2 sentences")
    motivation: str
    secret: str = Field(description="Something hidden that can surface later; empty string if none")
    knowledge: list[str] = Field(description="1-3 things this character knows at the start that others may not")


class BeatSpec(BaseModel):
    act: int = Field(description="Chapter number: 1, 2 or 3")
    title: str
    description: str = Field(description="What must happen in this beat, 1-2 sentences")
    tension_target: int = Field(description="Desired tension from 1 (calm) to 10 (climax)")


class InitialStateSpec(BaseModel):
    location: str
    time: str
    situation: str = Field(description="What is happening as the story opens")
    protagonist_condition: str
    inventory: list[str]
    open_threads: list[str] = Field(description="Questions or tensions the opening sets up")


# --------------------------------------------------------------------------- World Architect


class WorldBlueprint(BaseModel):
    """The fixed world, its people and the opening situation."""

    language: str = Field(description="Language to write the story in")
    title: str
    logline: str
    genre: str
    tone: str
    setting: str = Field(description="Era, place and atmosphere in 2-4 sentences")
    world_rules: list[str] = Field(description="4-7 fixed laws of this world that must never be broken")
    locations: list[LocationSpec]
    themes: list[str]
    narrative_style: str = Field(description="Point of view, tense and prose style")
    protagonist: CharacterSpec
    characters: list[CharacterSpec] = Field(description="3-5 supporting characters, not the protagonist")
    initial_state: InitialStateSpec


# --------------------------------------------------------------------------- Plot Planner


class PlotPlan(BaseModel):
    """The main line of the story: chapters, the central conflict and the key events."""

    central_conflict: str = Field(description="The core conflict driving the story, in one or two sentences")
    beats: list[BeatSpec] = Field(
        description="Ordered beats across three chapters, ending in a resolution beat. When re-planning, "
                    "only the beats that are not yet completed"
    )
    target_turns: int = Field(description="Planned number of player turns, between 6 and 14")
    pacing_style: str = Field(description="How tension should rise and fall over the story")
    rationale: str = Field(description="One or two sentences on why the plan is shaped this way")


# --------------------------------------------------------------------------- Intent Interpreter


class IntentReading(BaseModel):
    """What the player meant, and what the system should do next."""

    intent_type: Literal["action", "dialogue", "question", "story_change", "end_request", "unclear", "rule_conflict"]
    interpreted_intent: str = Field(description="What the player wants, in story terms")
    player_action: str = Field(description="The protagonist's in-story action or words, restated concretely; "
                                           "empty if the input is not an in-story action")
    action: Literal["advance", "clarify", "revise", "conclude"]
    rationale: str = Field(description="One or two sentences explaining the decision")
    clarifying_question: str = Field(description="Question for the player if action is clarify, else empty")
    clarifying_options: list[str] = Field(description="2-3 suggested answers if action is clarify, else empty")
    revision_scope: Literal["none", "outline", "last_segment", "both"]
    revision_instructions: str = Field(description="How to rewrite the last scene if it is being revised, else empty")
    plot_change_request: str = Field(description="If the plot should change direction: what the player wants it "
                                                 "to become, else empty")


# --------------------------------------------------------------------------- Scene Director


class ScenePlan(BaseModel):
    """The concrete plan for one scene, which the Narrator turns into prose."""

    scene_goal: str = Field(description="What this scene must accomplish for the story")
    location: str
    outcome: str = Field(description="What the player's action achieves, including any complication")
    events: list[str] = Field(description="3-6 concrete events, in order, that the scene must show")
    involved_characters: list[str] = Field(description="Cast members who act or speak in this scene, exactly as "
                                                       "named in the cast; never the protagonist")
    pivotal: bool = Field(description="True only for a turning point: a revelation, betrayal, death or "
                                      "irreversible choice")
    tension: int = Field(description="Tension of this scene from 1 to 10")
    ending_hook: str = Field(description="The moment the scene ends on")
    narrator_notes: str = Field(description="Short notes on mood, rhythm and focus for the Narrator")


# --------------------------------------------------------------------------- Character Agent


class NPCAction(BaseModel):
    name: str
    goal_in_scene: str = Field(description="What this character wants from this scene")
    knows: str = Field(description="What they know that matters here, from their own point of view")
    feels: str = Field(description="Their emotional state toward the protagonist and the situation")
    action: str = Field(description="What they do in the scene")
    says: str = Field(description="The gist of what they say, in their own voice")
    hides: str = Field(description="What they conceal or lie about; empty if nothing")


class CharacterActions(BaseModel):
    characters: list[NPCAction]


# --------------------------------------------------------------------------- Narrative Critic


class CriticReport(BaseModel):
    """A review of a drafted scene before it becomes canon."""

    verdict: Literal["pass", "revise"] = Field(description="revise only for problems worth a rewrite")
    causality_issues: list[str] = Field(description="Events that do not follow from what came before")
    consistency_issues: list[str] = Field(description="Breaks with world rules, established facts or characters")
    agency_issues: list[str] = Field(description="Places where the prose decides the protagonist's choices")
    quality_issues: list[str] = Field(description="Serious craft problems such as meta-commentary or flat prose")
    rewrite_guidance: str = Field(description="Concrete guidance for the rewrite; empty if the verdict is pass")


# --------------------------------------------------------------------------- Chronicler


class CharacterUpdate(BaseModel):
    name: str
    status: str = Field(description="Where they are and what condition they are in")
    disposition: str = Field(description="Their current attitude toward the protagonist")
    learned: list[str] = Field(description="New things this character now knows; empty if nothing")
    memory: str = Field(description="What they will remember about this scene; empty if nothing notable")


class EventSpec(BaseModel):
    description: str
    significance: Literal["minor", "major", "critical"]
    consequences: str


class Chronicle(BaseModel):
    """The Chronicler's updated picture of the story after a new scene."""

    location: str
    time: str
    situation: str = Field(description="Where things stand at the end of the scene")
    protagonist_condition: str
    inventory: list[str]
    present_characters: list[str]
    character_updates: list[CharacterUpdate] = Field(description="Every named character whose situation is known")
    open_threads: list[str]
    tension: int = Field(description="Current tension from 1 to 10")
    summary: str = Field(description="Story so far in at most 120 words")
    new_key_events: list[EventSpec] = Field(description="Only events that matter later; may be empty")
    beat_completed: bool = Field(description="True if the current outline beat's goal has been achieved")
    major_turn: bool = Field(description="True only if the story's direction changed so much that the remaining "
                                         "outline no longer fits")
    suggested_choices: list[str] = Field(description="2-4 distinct, concrete things the protagonist could do next")
