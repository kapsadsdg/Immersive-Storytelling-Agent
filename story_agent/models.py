"""Domain model: everything the agent knows about one story session.

The split mirrors the requirements:
  * WorldBible   - fixed world settings (frozen after creation)
  * StoryState   - current state tracking (rewritten every turn)
  * KeyEvent     - the record of key events (append-only, except on revision)
  * PlotOutline  - the adjustable plot outline (future beats can be revised)
  * DecisionRecord - each turn's decision and the trace of which agents ran
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

Action = Literal["opening", "advance", "clarify", "revise", "conclude"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- world


class Location(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    description: str


class WorldBible(BaseModel):
    """Fixed world settings. Created once from the theme and never edited."""

    model_config = ConfigDict(frozen=True)

    title: str
    logline: str
    genre: str
    tone: str
    setting: str
    world_rules: tuple[str, ...]
    locations: tuple[Location, ...]
    themes: tuple[str, ...]
    narrative_style: str
    language: str = "English"


class Character(BaseModel):
    """A member of the core cast. Identity is fixed; their live status lives in StoryState."""

    model_config = ConfigDict(frozen=True)

    name: str
    role: str
    description: str
    motivation: str
    secret: str = ""


class PacingPlan(BaseModel):
    target_turns: int
    style: str

    @property
    def max_turns(self) -> int:
        """Hard ceiling after which the story is wrapped up regardless of the outline."""
        return self.target_turns + max(3, self.target_turns // 2)


# --------------------------------------------------------------------------- outline


class Beat(BaseModel):
    id: str
    act: int
    title: str
    description: str
    tension_target: int
    status: Literal["pending", "active", "done"] = "pending"
    turns_spent: int = 0


class PlotOutline(BaseModel):
    beats: list[Beat]
    central_conflict: str = ""
    revision_log: list[str] = Field(default_factory=list)

    def current_beat(self) -> Beat | None:
        for beat in self.beats:
            if beat.status != "done":
                return beat
        return None

    def done_count(self) -> int:
        return sum(1 for b in self.beats if b.status == "done")

    def is_complete(self) -> bool:
        return all(b.status == "done" for b in self.beats)

    def activate_next(self) -> None:
        beat = self.current_beat()
        if beat is not None and beat.status == "pending":
            beat.status = "active"


# --------------------------------------------------------------------------- state


class CharacterState(BaseModel):
    """Live state of one cast member: kept by the Chronicler, read by the Character Agent."""

    MAX_KNOWLEDGE: ClassVar[int] = 12
    MAX_MEMORIES: ClassVar[int] = 8

    name: str
    status: str
    disposition: str
    knowledge: list[str] = Field(default_factory=list)
    memories: list[str] = Field(default_factory=list)


class StoryState(BaseModel):
    """Live state of the story, rewritten by the Chronicler after every segment."""

    turn: int = 0
    location: str
    time: str
    situation: str
    protagonist_condition: str
    inventory: list[str] = Field(default_factory=list)
    present_characters: list[str] = Field(default_factory=list)
    characters: list[CharacterState] = Field(default_factory=list)
    open_threads: list[str] = Field(default_factory=list)
    tension: int = 3
    summary: str = ""


class KeyEvent(BaseModel):
    turn: int
    description: str
    significance: Literal["minor", "major", "critical"]
    consequences: str


# --------------------------------------------------------------------------- history


class Segment(BaseModel):
    turn: int
    kind: Literal["opening", "advance", "revision", "ending"]
    text: str
    user_input: str = ""


class AgentStep(BaseModel):
    """One entry in a turn's dispatch trace: an agent that ran, or was skipped, and why."""

    agent: str
    ran: bool
    reason: str


class DecisionRecord(BaseModel):
    """Audit trail of one turn: what was decided, why, and which agents ran."""

    turn: int
    user_input: str
    action: Action
    rationale: str
    revision_scope: str = "none"
    overridden_by_policy: str = ""
    trace: list[AgentStep] = Field(default_factory=list)
    timestamp: str = Field(default_factory=utc_now)


class Snapshot(BaseModel):
    """State before a segment was written, so that segment can be revised (retconned)."""

    state: StoryState
    outline: PlotOutline
    events_len: int
    segments_len: int
    choices: list[str]


# --------------------------------------------------------------------------- session


class Story(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    theme: str
    status: Literal["active", "awaiting_clarification", "concluded"] = "active"
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    world: WorldBible
    protagonist: Character
    characters: list[Character]
    pacing: PacingPlan
    outline: PlotOutline
    state: StoryState
    events: list[KeyEvent] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    snapshots: list[Snapshot] = Field(default_factory=list)

    choices: list[str] = Field(default_factory=list)
    pending_question: str = ""

    MAX_SNAPSHOTS: ClassVar[int] = 4

    def push_snapshot(self) -> None:
        self.snapshots.append(
            Snapshot(
                state=self.state.model_copy(deep=True),
                outline=self.outline.model_copy(deep=True),
                events_len=len(self.events),
                segments_len=len(self.segments),
                choices=list(self.choices),
            )
        )
        del self.snapshots[: -self.MAX_SNAPSHOTS]

    def restore_last_snapshot(self) -> Snapshot | None:
        if not self.snapshots:
            return None
        snap = self.snapshots.pop()
        self.state = snap.state
        self.outline = snap.outline
        del self.events[snap.events_len :]
        del self.segments[snap.segments_len :]
        self.choices = snap.choices
        return snap

    def all_character_names(self) -> list[str]:
        return [self.protagonist.name] + [c.name for c in self.characters]
