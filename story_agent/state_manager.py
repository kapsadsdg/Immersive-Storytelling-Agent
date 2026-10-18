"""State Manager (deterministic): every change to the story's canonical state goes through here.

The agents propose (a world, a plot plan, a chronicle of what changed); this
module validates those proposals and commits them. Keeping all state
transitions in one place makes them testable without a model:

* create_story        - World Architect + Plot Planner output -> a fresh Story
* replace_future_beats- Plot Planner re-plan -> new beats; completed beats stay fixed
* apply_chronicle     - Chronicler output -> new state, key events, NPC minds, beat progress
* snapshots           - Story.push_snapshot / restore_last_snapshot, used for retcons
"""

from __future__ import annotations

from .llm.base import LLMError
from .models import (
    Beat,
    Character,
    CharacterState,
    KeyEvent,
    Location,
    PacingPlan,
    PlotOutline,
    Story,
    StoryState,
    WorldBible,
)
from .schemas import BeatSpec, CharacterSpec, Chronicle, PlotPlan, WorldBlueprint


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def beats_from_specs(specs: list[BeatSpec], start_index: int = 1) -> list[Beat]:
    return [
        Beat(
            id=f"b{start_index + i}",
            act=clamp(spec.act, 1, 3),
            title=spec.title.strip(),
            description=spec.description.strip(),
            tension_target=clamp(spec.tension_target, 1, 10),
        )
        for i, spec in enumerate(s for s in specs if s.title.strip())
    ]


def _character(spec: CharacterSpec) -> Character:
    return Character(
        name=spec.name.strip(),
        role=spec.role.strip(),
        description=spec.description.strip(),
        motivation=spec.motivation.strip(),
        secret=spec.secret.strip(),
    )


def _merge(existing: list[str], new: list[str], limit: int) -> list[str]:
    """Append new items that are not already present (case-insensitive), keeping the most recent `limit`."""
    seen = {item.lower() for item in existing}
    merged = list(existing)
    for item in (n.strip() for n in new):
        if item and item.lower() not in seen:
            merged.append(item)
            seen.add(item.lower())
    return merged[-limit:]


# --------------------------------------------------------------------------- creation


def create_story(theme: str, bp: WorldBlueprint, plan: PlotPlan, language: str = "auto") -> Story:
    """Validate the World Architect's and Plot Planner's output and build a fresh Story."""
    beats = beats_from_specs(plan.beats)
    if not beats:
        raise LLMError("The Plot Planner produced an empty outline.")
    beats[0].status = "active"

    protagonist = _character(bp.protagonist)
    seen = {protagonist.name.lower()}
    cast: list[Character] = []
    specs: dict[str, CharacterSpec] = {}
    for spec in bp.characters:
        name = spec.name.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            cast.append(_character(spec))
            specs[name] = spec

    world = WorldBible(
        title=bp.title.strip(),
        logline=bp.logline.strip(),
        genre=bp.genre.strip(),
        tone=bp.tone.strip(),
        setting=bp.setting.strip(),
        world_rules=tuple(r.strip() for r in bp.world_rules if r.strip()),
        locations=tuple(Location(name=l.name.strip(), description=l.description.strip()) for l in bp.locations),
        themes=tuple(t.strip() for t in bp.themes if t.strip()),
        narrative_style=bp.narrative_style.strip(),
        language=language if language.lower() != "auto" else (bp.language.strip() or "English"),
    )

    init = bp.initial_state
    state = StoryState(
        turn=0,
        location=init.location,
        time=init.time,
        situation=init.situation,
        protagonist_condition=init.protagonist_condition,
        inventory=list(init.inventory),
        present_characters=[],
        characters=[
            CharacterState(name=c.name, status="not yet met", disposition="unknown",
                           knowledge=_merge([], specs[c.name].knowledge, CharacterState.MAX_KNOWLEDGE))
            for c in cast
        ],
        open_threads=list(init.open_threads),
        tension=clamp(beats[0].tension_target, 1, 10),
        summary="",
    )

    return Story(
        theme=theme.strip(),
        world=world,
        protagonist=protagonist,
        characters=cast,
        pacing=PacingPlan(target_turns=clamp(plan.target_turns, 4, 20), style=plan.pacing_style.strip()),
        outline=PlotOutline(beats=beats, central_conflict=plan.central_conflict.strip()),
        state=state,
    )


# --------------------------------------------------------------------------- outline


def replace_future_beats(story: Story, plan: PlotPlan, reason: str) -> bool:
    """Swap every not-yet-completed beat for the Plot Planner's new ones. Completed beats are history."""
    outline = story.outline
    done = [b for b in outline.beats if b.status == "done"]
    old_current = outline.current_beat()
    next_id = max((int(b.id[1:]) for b in outline.beats if b.id[1:].isdigit()), default=0) + 1
    new = beats_from_specs(plan.beats, start_index=next_id)
    if not new:
        return False
    new[0].status = "active"
    if old_current and new[0].title.strip().lower() == old_current.title.strip().lower():
        new[0].turns_spent = old_current.turns_spent
    outline.beats = done + new
    if plan.central_conflict.strip():
        outline.central_conflict = plan.central_conflict.strip()
    outline.revision_log.append(f"Turn {story.state.turn}: {reason}")
    return True


# --------------------------------------------------------------------------- chronicle


def apply_chronicle(story: Story, c: Chronicle, ending: bool) -> None:
    """Commit the Chronicler's record: state, key events, character minds and beat progress."""
    beat = story.outline.current_beat()
    if beat is not None:
        beat.turns_spent += 1

    known = {cs.name.lower(): cs for cs in story.state.characters}
    protagonist = story.protagonist.name.lower()
    for upd in c.character_updates:
        name = upd.name.strip()
        if not name or name.lower() == protagonist:
            continue
        old = known.get(name.lower())
        known[name.lower()] = CharacterState(
            name=old.name if old else name,
            status=upd.status,
            disposition=upd.disposition,
            knowledge=_merge(old.knowledge if old else [], upd.learned, CharacterState.MAX_KNOWLEDGE),
            memories=_merge(old.memories if old else [], [upd.memory], CharacterState.MAX_MEMORIES),
        )

    turn = story.state.turn + 1
    story.state = StoryState(
        turn=turn,
        location=c.location or story.state.location,
        time=c.time or story.state.time,
        situation=c.situation,
        protagonist_condition=c.protagonist_condition,
        inventory=[i for i in c.inventory if i.strip()],
        present_characters=[p for p in c.present_characters if p.strip()],
        characters=list(known.values()),
        open_threads=[t for t in c.open_threads if t.strip()],
        tension=clamp(c.tension, 1, 10),
        summary=c.summary.strip() or story.state.summary,
    )
    story.events.extend(
        KeyEvent(turn=turn, description=e.description, significance=e.significance, consequences=e.consequences)
        for e in c.new_key_events
        if e.description.strip()
    )

    if ending:
        if beat is not None:
            beat.status = "done"
        story.status = "concluded"
        story.choices = []
        return
    if c.beat_completed and beat is not None:
        beat.status = "done"
        story.outline.activate_next()
    story.choices = [ch for ch in c.suggested_choices if ch.strip()][:4]
