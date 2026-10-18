"""Context Builder (deterministic): renders pieces of a Story into compact prompt text.

Every agent sees the same canonical rendering of the world, state, outline and
event record, so they share one consistent picture of "what is true".
Long histories are handled by sending the rolling summary, the key-event
record and only the most recent segments verbatim.
"""

from __future__ import annotations

from .models import CharacterState, KeyEvent, PlotOutline, Segment, StoryState, Story, WorldBible
from .schemas import CharacterActions, ScenePlan, WorldBlueprint


def _bullets(items, empty: str = "(none)") -> str:
    items = [str(i) for i in items if str(i).strip()]
    return "\n".join(f"- {i}" for i in items) if items else empty


def render_world(world: WorldBible) -> str:
    locations = _bullets(f"{loc.name}: {loc.description}" for loc in world.locations)
    return (
        f"Title: {world.title}\n"
        f"Logline: {world.logline}\n"
        f"Genre: {world.genre} | Tone: {world.tone} | Language: {world.language}\n"
        f"Setting: {world.setting}\n"
        f"Narrative style: {world.narrative_style}\n"
        f"Themes: {', '.join(world.themes)}\n"
        f"WORLD RULES (fixed, never break):\n{_bullets(world.world_rules)}\n"
        f"Locations:\n{locations}"
    )


def render_cast(story: Story, include_secrets: bool = True) -> str:
    lines = []
    p = story.protagonist
    lines.append(f"- {p.name} (PROTAGONIST, played by the player): {p.description} Goal: {p.motivation}")
    for c in story.characters:
        line = f"- {c.name} ({c.role}): {c.description} Wants: {c.motivation}"
        if include_secrets and c.secret:
            line += f" [Secret: {c.secret}]"
        lines.append(line)
    return "\n".join(lines)


def render_state(state: StoryState) -> str:
    chars = _bullets(f"{c.name}: {c.status}; toward protagonist: {c.disposition}" for c in state.characters)
    return (
        f"Turn: {state.turn}\n"
        f"Location: {state.location}\n"
        f"Time: {state.time}\n"
        f"Situation: {state.situation}\n"
        f"Protagonist condition: {state.protagonist_condition}\n"
        f"Inventory: {', '.join(state.inventory) or '(nothing)'}\n"
        f"Present: {', '.join(state.present_characters) or '(no one else)'}\n"
        f"Tension: {state.tension}/10\n"
        f"Characters:\n{chars}\n"
        f"Open threads:\n{_bullets(state.open_threads)}\n"
        f"Story so far: {state.summary or '(the story is just beginning)'}"
    )


def render_outline(outline: PlotOutline) -> str:
    marks = {"done": "[x]", "active": "[>]", "pending": "[ ]"}
    beats = "\n".join(
        f"{marks[b.status]} {b.id} (Chapter {b.act}, tension {b.tension_target}): {b.title} - {b.description}"
        for b in outline.beats
    )
    conflict = f"Central conflict: {outline.central_conflict}\n" if outline.central_conflict else ""
    return conflict + beats


def render_events(events: list[KeyEvent], limit: int = 15) -> str:
    if not events:
        return "(no key events yet)"
    shown = events[-limit:]
    return "\n".join(
        f"- Turn {e.turn} [{e.significance}]: {e.description} -> {e.consequences}" for e in shown
    )


def render_recent_segments(segments: list[Segment], count: int = 2) -> str:
    if not segments:
        return "(nothing has been narrated yet)"
    parts = []
    for seg in segments[-count:]:
        header = f"[Turn {seg.turn}]"
        if seg.user_input:
            header += f" Player: {seg.user_input}"
        parts.append(f"{header}\n{seg.text}")
    return "\n\n".join(parts)


def render_story_context(story: Story, segments: int = 2) -> str:
    return (
        f"=== WORLD BIBLE ===\n{render_world(story.world)}\n\n"
        f"=== CAST ===\n{render_cast(story)}\n\n"
        f"=== CURRENT STATE ===\n{render_state(story.state)}\n\n"
        f"=== PLOT OUTLINE ([x] done, [>] current, [ ] upcoming) ===\n{render_outline(story.outline)}\n\n"
        f"=== KEY EVENTS ===\n{render_events(story.events)}\n\n"
        f"=== MOST RECENT SEGMENTS ===\n{render_recent_segments(story.segments, segments)}"
    )


def render_character_minds(characters: list[CharacterState], names: list[str] | None = None) -> str:
    """What each character knows and remembers: the private context the Character Agent plays from."""
    wanted = {n.lower() for n in names} if names else None
    parts = []
    for c in characters:
        if wanted is not None and c.name.lower() not in wanted:
            continue
        parts.append(
            f"{c.name} ({c.status}; toward protagonist: {c.disposition})\n"
            f"  Knows: {'; '.join(c.knowledge) or '(nothing special)'}\n"
            f"  Remembers: {'; '.join(c.memories[-4:]) or '(nothing yet)'}"
        )
    return "\n".join(parts) or "(no characters)"


def render_blueprint(bp: WorldBlueprint) -> str:
    """The World Architect's output, before a Story exists (read by the first Plot Planner call)."""
    cast = "\n".join(f"- {c.name} ({c.role}): {c.description} Wants: {c.motivation} [Secret: {c.secret or 'none'}]"
                     for c in bp.characters)
    p, init = bp.protagonist, bp.initial_state
    return (
        f"Title: {bp.title}\nLogline: {bp.logline}\n"
        f"Genre: {bp.genre} | Tone: {bp.tone} | Language: {bp.language}\n"
        f"Setting: {bp.setting}\nWORLD RULES:\n{_bullets(bp.world_rules)}\n"
        f"Protagonist: {p.name}: {p.description} Goal: {p.motivation}\nCast:\n{cast}\n"
        f"Opening: {init.situation} ({init.location}, {init.time})\nOpen threads:\n{_bullets(init.open_threads)}"
    )


def render_scene_plan(plan: ScenePlan) -> str:
    events = "\n".join(f"{i}. {e}" for i, e in enumerate(plan.events, 1))
    return (
        f"Scene goal: {plan.scene_goal}\n"
        f"Location: {plan.location}\n"
        f"Outcome of the player's action: {plan.outcome}\n"
        f"Events, in order:\n{events}\n"
        f"Characters taking part: {', '.join(plan.involved_characters) or '(none)'}\n"
        f"Tension: {plan.tension}/10{' (pivotal scene)' if plan.pivotal else ''}\n"
        f"End on: {plan.ending_hook}\n"
        f"Notes: {plan.narrator_notes}"
    )


def render_npc_actions(actions: CharacterActions | None) -> str:
    if actions is None or not actions.characters:
        return "(no character notes for this scene)"
    return "\n".join(
        f"- {a.name}: wants {a.goal_in_scene}. Knows: {a.knows}. Feels: {a.feels}. Does: {a.action}. "
        f"Says: {a.says}.{' Hides: ' + a.hides + '.' if a.hides else ''}"
        for a in actions.characters
    )
