"""The eight-agent architecture: on-demand dispatch and the deterministic core modules."""

from __future__ import annotations

import pytest

from story_agent import scheduler, state_manager
from story_agent.config import AGENT_LABELS, Settings
from story_agent.engine import StoryEngine
from story_agent.llm.mock_llm import MockLLM
from story_agent.models import CharacterState
from story_agent.pacing import assess
from story_agent.policy import apply_policy
from story_agent.schemas import BeatSpec, CharacterUpdate, Chronicle, PlotPlan, ScenePlan

ALL_AGENTS = set(AGENT_LABELS)


@pytest.fixture()
def llm():
    return MockLLM()


@pytest.fixture()
def engine(llm, tmp_path):
    return StoryEngine(llm, Settings(provider="mock", save_dir=tmp_path))


def ran(story) -> set[str]:
    return {s.agent for s in story.decisions[-1].trace if s.ran}


def plan(**overrides) -> ScenePlan:
    base = dict(scene_goal="g", location="l", outcome="o", events=["e"], involved_characters=[], pivotal=False,
                tension=5, ending_hook="h", narrator_notes="n")
    base.update(overrides)
    return ScenePlan(**base)


# --------------------------------------------------------------------------- dispatch, end to end


def test_story_creation_runs_the_creation_agents(engine):
    story, result = engine.start("a lighthouse")
    assert ran(story) == ALL_AGENTS - {"intent_interpreter"}
    assert story.outline.central_conflict
    assert story.decisions[0].action == "opening"
    assert {s.agent for s in story.decisions[0].trace} == ALL_AGENTS  # every agent accounted for


def test_picking_a_suggested_choice_skips_the_intent_interpreter(engine, llm):
    story, _ = engine.start("t")
    engine.play(story, "1. climb the tower!")  # numbering, case and punctuation are ignored
    assert "intent_interpreter" not in ran(story)
    assert ran(story) == {"scene_director", "narrator", "chronicler"}


def test_free_text_runs_the_intent_interpreter(engine):
    story, _ = engine.start("t")
    engine.play(story, "I light my lamp and listen")
    assert "intent_interpreter" in ran(story)


def test_character_agent_runs_only_when_cast_members_take_part(engine):
    story, _ = engine.start("t")
    engine.play(story, "I walk alone along the quay")
    assert "character_agent" not in ran(story)
    engine.play(story, "I ask Ilse about her ship")
    assert "character_agent" in ran(story)


def test_critic_reviews_key_moments_only(engine):
    story, _ = engine.start("t")
    engine.play(story, "I walk alone along the quay")
    assert "narrative_critic" not in ran(story)
    engine.play(story, "I confront the Warden")  # the mock plans this as a pivotal scene
    assert "narrative_critic" in ran(story)


def test_critic_is_skipped_when_self_correction_is_off(llm, tmp_path):
    eng = StoryEngine(llm, Settings(provider="mock", save_dir=tmp_path, self_correction=False))
    story, _ = eng.start("t")
    step = next(s for s in story.decisions[0].trace if s.agent == "narrative_critic")
    assert not step.ran and "turned off" in step.reason


def test_major_turn_makes_the_plot_planner_replan(engine):
    story, _ = engine.start("t")
    result = engine.play(story, "I betray Ilse to the Warden")
    assert "plot_planner" in ran(story)
    assert "Aftermath" in [b.title for b in story.outline.beats]
    assert any("re-planned" in n for n in result.notes)


def test_plot_change_request_goes_to_the_plot_planner(engine):
    story, _ = engine.start("t")
    engine.play(story, "Climb the tower")
    engine.play(story, "from now on the plot should involve pirates, revise it")
    assert {"intent_interpreter", "plot_planner"} <= ran(story)
    assert story.outline.beats[0].status == "done"  # completed beats are history
    assert "A new direction" in [b.title for b in story.outline.beats]


def test_clarifying_question_runs_nothing_else(engine):
    story, _ = engine.start("t")
    engine.play(story, "?")
    assert ran(story) == {"intent_interpreter"}


def test_end_command_is_not_treated_as_an_in_story_action(engine):
    story, _ = engine.start("t")
    result = engine.play(story, scheduler.END_REQUEST)
    assert result.action == "conclude" and "intent_interpreter" not in ran(story)
    knowledge = " ".join(k for c in story.state.characters for k in c.knowledge + c.memories)
    assert "end the story" not in knowledge.lower()


def test_trace_is_saved_and_reported(engine):
    story, _ = engine.start("t")
    events = []
    result = engine.play(story, "Climb the tower", on_event=events.append)
    trace_events = [e for e in events if e.kind == "trace"]
    assert trace_events and trace_events[0].data["ran"] == result.agents_ran
    assert "Character Agent" in trace_events[0].data["skipped"]
    labels = {e.data["label"] for e in events if e.kind == "status"}
    assert labels <= set(AGENT_LABELS.values())


# --------------------------------------------------------------------------- Scheduler rules


def test_fast_intent_rules(engine):
    story, _ = engine.start("t")
    assert scheduler.fast_intent(story, "Walk into the fog.").player_action == "Walk into the fog"
    assert scheduler.fast_intent(story, scheduler.END_REQUEST).action == "conclude"
    assert scheduler.fast_intent(story, "Walk into the fog and then sing") is None


def test_scene_characters_are_filtered_and_capped(engine):
    story, _ = engine.start("t")
    p = plan(involved_characters=["ilse", "Wren", "Nobody", "Ilse", "Old Marsh", "The Warden"])
    assert scheduler.scene_characters(story, p) == ["Ilse", "Old Marsh", "The Warden"]  # no protagonist, no strangers


@pytest.mark.parametrize("mode, overrides, text, expected", [
    ("opening", {}, "word " * 200, "opening"),
    ("ending", {}, "word " * 200, "ending"),
    ("revision", {}, "word " * 200, "Rewritten"),
    ("advance", {"pivotal": True}, "word " * 200, "turning point"),
    ("advance", {"tension": 9}, "word " * 200, "High-tension"),
    ("advance", {}, "too short", "short"),
    ("advance", {}, "word " * 1000, "long"),
    ("advance", {}, "word " * 150 + "as the player wishes", "fourth wall"),
    ("advance", {}, "word " * 200, ""),
])
def test_critic_triggers(mode, overrides, text, expected):
    reason = scheduler.critic_reason(mode, plan(**overrides), text)
    assert (expected in reason) if expected else reason == ""


# --------------------------------------------------------------------------- Policy Guard and State Manager


def test_policy_drops_an_outline_revision_without_any_request(engine):
    story, _ = engine.start("t")
    reading = scheduler.blank_reading(action="revise", revision_scope="outline", interpreted_intent="")
    outcome = apply_policy(story, reading, assess(story))
    assert outcome.action == "advance" and "nothing concrete" in outcome.note


def test_npc_knowledge_and_memories_are_merged_and_capped(engine):
    story, _ = engine.start("t")
    base = dict(location="l", time="t", situation="s", protagonist_condition="ok", inventory=[], present_characters=[],
                open_threads=[], tension=5, summary="x", new_key_events=[], beat_completed=False, major_turn=False,
                suggested_choices=["a", "b"])
    for i in range(20):
        c = Chronicle(character_updates=[CharacterUpdate(name="Ilse", status="here", disposition="cold",
                                                         learned=[f"fact {i}", "fact 0"], memory=f"scene {i}")], **base)
        state_manager.apply_chronicle(story, c, ending=False)
    ilse = next(c for c in story.state.characters if c.name == "Ilse")
    assert len(ilse.knowledge) == CharacterState.MAX_KNOWLEDGE and ilse.knowledge[-1] == "fact 19"
    assert len(ilse.memories) == CharacterState.MAX_MEMORIES and ilse.memories[-1] == "scene 19"
    assert len(set(k.lower() for k in ilse.knowledge)) == len(ilse.knowledge)  # no duplicates


def test_replacing_future_beats_keeps_history_and_unique_ids(engine):
    story, _ = engine.start("t")
    engine.play(story, "Climb the tower")
    new_plan = PlotPlan(central_conflict="A new conflict", target_turns=6, pacing_style="p", rationale="r",
                        beats=[BeatSpec(act=2, title="X", description="d", tension_target=5)])
    assert state_manager.replace_future_beats(story, new_plan, "test")
    titles = [b.title for b in story.outline.beats]
    assert titles == ["The silent bell", "X"] and story.outline.central_conflict == "A new conflict"
    assert len({b.id for b in story.outline.beats}) == len(story.outline.beats)


def test_legacy_effort_settings_still_apply(tmp_path, monkeypatch):
    for var in ("EFFORT_ARCHITECT", "EFFORT_DIRECTOR", "EFFORT_SCENE_DIRECTOR", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=mock\nEFFORT_ARCHITECT=high\nEFFORT_DIRECTOR=medium\nEFFORT_SCENE_DIRECTOR=low\n",
                   encoding="utf-8")
    s = Settings.from_env(env, template_file=tmp_path / "missing")
    assert s.efforts["world_architect"] == s.efforts["plot_planner"] == "high"
    assert s.efforts["intent_interpreter"] == "medium"
    assert s.efforts["scene_director"] == "low"  # the specific name wins over the legacy one
