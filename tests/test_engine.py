"""Behavioural tests for the agent loop, run offline against the mock model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from story_agent.agents.narrator import clean_prose
from story_agent.config import Settings
from story_agent.engine import StoryEngine
from story_agent.llm.mock_llm import MockLLM
from story_agent.pacing import assess
from story_agent.storage import JSONStorage


@pytest.fixture()
def engine(tmp_path):
    return StoryEngine(MockLLM(), Settings(provider="mock", save_dir=tmp_path), JSONStorage(tmp_path))


def test_theme_generates_world_cast_outline_and_opening(engine):
    story, result = engine.start("a lighthouse at the end of the world")
    assert result.action == "opening"
    assert story.world.world_rules and story.characters and len(story.outline.beats) >= 3
    assert story.outline.beats[0].status == "active"
    assert story.state.turn == 1 and len(story.segments) == 1
    assert story.events, "the opening should be recorded as a key event"
    assert result.choices


def test_world_settings_are_frozen(engine):
    story, _ = engine.start("anything")
    with pytest.raises(ValidationError):
        story.world.title = "Something else"


def test_advance_updates_state_events_and_outline(engine):
    story, _ = engine.start("theme")
    result = engine.play(story, "Climb the tower")
    assert result.action == "advance"
    assert story.state.turn == 2
    assert story.segments[-1].user_input == "Climb the tower"
    assert story.outline.beats[0].status == "done" and story.outline.beats[1].status == "active"


def test_clarify_does_not_change_state_and_is_never_asked_twice(engine):
    story, _ = engine.start("theme")
    before = story.state.model_copy(deep=True)
    result = engine.play(story, "?")
    assert result.action == "clarify" and story.status == "awaiting_clarification"
    assert story.state == before and len(story.segments) == 1

    result = engine.play(story, "??")  # still unclear: policy forbids a second question
    assert result.action == "advance"
    assert story.status == "active"
    assert "already asked" in story.decisions[-1].overridden_by_policy


def test_revise_last_segment_rolls_back_and_rewrites(engine):
    story, _ = engine.start("theme")
    engine.play(story, "Climb the tower")
    turn, events = story.state.turn, len(story.events)
    result = engine.play(story, "rewrite that so the stairs are broken")
    assert result.action == "revise"
    assert story.segments[-1].kind == "revision"
    assert story.segments[-1].user_input == "Climb the tower"  # the original action is kept
    assert story.state.turn == turn and len(story.events) == events
    assert len(story.segments) == 2


def test_revise_outline_keeps_completed_beats(engine):
    story, _ = engine.start("theme")
    engine.play(story, "Climb the tower")
    done_titles = [b.title for b in story.outline.beats if b.status == "done"]
    result = engine.play(story, "revise the plot from now on: add pirates")
    assert result.action == "revise"
    titles = [b.title for b in story.outline.beats]
    assert titles[: len(done_titles)] == done_titles
    assert "A new direction" in titles
    assert story.outline.revision_log
    assert len({b.id for b in story.outline.beats}) == len(story.outline.beats), "beat ids must stay unique"


def test_player_can_conclude(engine):
    story, _ = engine.start("theme")
    result = engine.play(story, "I want to end the story now")
    assert result.action == "conclude" and story.status == "concluded"
    assert story.segments[-1].kind == "ending" and result.choices == []
    assert engine.play(story, "anything").action == "ended"


def test_story_concludes_when_outline_is_complete(engine):
    story, _ = engine.start("theme")
    actions = []
    for i in range(20):
        result = engine.play(story, f"Do something brave number {i}")
        actions.append(result.action)
        if story.status == "concluded":
            break
    assert story.status == "concluded"
    assert actions[-1] == "conclude"
    assert story.outline.is_complete()
    assert story.state.turn <= story.pacing.max_turns + 1


def test_hard_turn_limit_forces_conclusion(engine):
    story, _ = engine.start("theme")
    story.state.turn = story.pacing.max_turns
    assert assess(story).must_conclude
    assert engine.play(story, "Look around").action == "conclude"


def test_critic_rejection_triggers_one_rewrite(tmp_path):
    llm = MockLLM(inject_critic_rejection=True)
    eng = StoryEngine(llm, Settings(provider="mock", save_dir=tmp_path), JSONStorage(tmp_path))
    story, result = eng.start("theme")
    assert any("Narrative Critic" in n for n in result.notes)
    assert [c for c in llm.calls if c == ("narrator", "text")] == [("narrator", "text")] * 2


def test_story_round_trips_through_storage(engine, tmp_path):
    story, _ = engine.start("theme")
    engine.play(story, "Climb the tower")
    loaded = JSONStorage(tmp_path).load(story.id)
    assert loaded == story
    assert JSONStorage(tmp_path).list()[0].id == story.id


def test_storage_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        JSONStorage(tmp_path).load("../secrets")


def test_prose_cleanup_keeps_bold_and_strips_other_markdown():
    raw = "## Chapter\n\n**The door** opens.\n\n- a bullet\n\n\n\n*Quietly*, you step in.\n\n---"
    assert clean_prose(raw) == "Chapter\n\n**The door** opens.\n\na bullet\n\nQuietly, you step in."


@pytest.mark.parametrize("raw, expected", [
    ("You find **the key**.", "You find **the key**."),
    ("A __hidden door__ swings open.", "A **hidden door** swings open."),
    ("**one** **two** **three** **four** **five**", "**one** **two** **three** four five"),  # cap of 3
    ("It was **unfinished.", "It was unfinished."),  # unbalanced marker
    ("**across\nlines**", "across\nlines"),  # bold may not cross a line break
    ("Empty **** marks and ** ** gaps.", "Empty marks and gaps."),
    ("*soft* and **loud**", "soft and **loud**"),  # italics stripped, bold intact
    ("A **  padded  ** word.", "A padded word."),  # markers touching spaces are stray, as in Markdown
    ("**I** see.", "**I** see."),  # single-character bold
    ("3 * 4 = 12", "3 * 4 = 12"),  # a lone asterisk is not markup
])
def test_prose_cleanup_bold_rules(raw, expected):
    assert clean_prose(raw) == expected


class PromptRecordingLLM(MockLLM):
    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    def structured(self, *, role, system, prompt, schema):
        self.prompts.append(prompt)
        return super().structured(role=role, system=system, prompt=prompt, schema=schema)


@pytest.mark.parametrize("language, expected_line, expected_world", [
    ("English", "STORY LANGUAGE: English", "English"),
    ("auto", "STORY LANGUAGE: the same language the theme is written in", "English"),  # mock answers English
    ("French", "STORY LANGUAGE: French", "French"),
])
def test_story_language_setting(tmp_path, language, expected_line, expected_world):
    llm = PromptRecordingLLM()
    eng = StoryEngine(llm, Settings(provider="mock", save_dir=tmp_path, story_language=language))
    story, _ = eng.start("a theme in any language")
    assert expected_line in llm.prompts[0]
    assert story.world.language == expected_world
