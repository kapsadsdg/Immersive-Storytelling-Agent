"""End-to-end test of the HTTP API and its streamed events, using the mock model."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from story_agent.config import Settings
from story_agent.llm.mock_llm import MockLLM
from story_agent.web.app import create_app


def events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_play_a_story_over_http(tmp_path):
    client = TestClient(create_app(Settings(provider="mock", save_dir=tmp_path), MockLLM()))
    assert client.get("/").status_code == 200
    assert client.get("/api/config").json()["provider"] == "mock"

    evs = events(client.post("/api/stories", json={"theme": "a haunted lighthouse"}))
    kinds = [e["type"] for e in evs]
    assert kinds[-1] == "done" and "delta" in kinds and "status" in kinds
    story = evs[-1]["story"]
    assert story["segments"] and story["pacing_report"] and "snapshots" not in story

    evs = events(client.post(f"/api/stories/{story['id']}/turns", json={"input": "Climb the tower"}))
    assert any(e["type"] == "decision" and e["action"] == "advance" for e in evs)
    assert evs[-1]["story"]["state"]["turn"] == 2

    assert client.get(f"/api/stories/{story['id']}").json()["id"] == story["id"]
    assert client.get("/api/stories").json()[0]["id"] == story["id"]
    assert client.get("/api/stories/nope").status_code == 404


def test_delete_story(tmp_path):
    client = TestClient(create_app(Settings(provider="mock", save_dir=tmp_path), MockLLM()))
    story_id = events(client.post("/api/stories", json={"theme": "a haunted lighthouse"}))[-1]["story"]["id"]
    assert [s["id"] for s in client.get("/api/stories").json()] == [story_id]

    assert client.delete(f"/api/stories/{story_id}").status_code == 204
    assert client.get("/api/stories").json() == []
    assert client.get(f"/api/stories/{story_id}").status_code == 404
    assert client.delete(f"/api/stories/{story_id}").status_code == 404
    assert client.delete("/api/stories/..%2Fsecret").status_code == 404


def test_delete_is_refused_while_a_turn_runs(tmp_path):
    app = create_app(Settings(provider="mock", save_dir=tmp_path), MockLLM())
    client = TestClient(app)
    story_id = events(client.post("/api/stories", json={"theme": "t"}))[-1]["story"]["id"]
    held = app.state.lock_for(story_id)  # simulate a turn in progress
    held.acquire()
    try:
        assert client.delete(f"/api/stories/{story_id}").status_code == 409
    finally:
        held.release()
    assert client.delete(f"/api/stories/{story_id}").status_code == 204


def test_audio_manifest_without_files(tmp_path):
    client = TestClient(create_app(Settings(provider="mock", save_dir=tmp_path), MockLLM(), audio_dir=tmp_path / "audio"))
    manifest = client.get("/api/audio").json()
    assert manifest["click"] is None
    assert manifest["thinking"] == {"calm": None, "tense": None, "climax": None, "default": None}


def test_audio_manifest_lists_and_serves_existing_files(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    for name in ("thinking.wav", "thinking-climax.ogg", "click.mp3", "click.wav", "notes.txt"):
        (audio / name).write_bytes(b"fake audio " + name.encode())
    client = TestClient(create_app(Settings(provider="mock", save_dir=tmp_path), MockLLM(), audio_dir=audio))

    manifest = client.get("/api/audio").json()
    assert manifest["click"] == "/audio/click.mp3"  # .mp3 is preferred when several formats exist
    assert manifest["thinking"] == {"calm": None, "tense": None,
                                    "climax": "/audio/thinking-climax.ogg", "default": "/audio/thinking.wav"}
    response = client.get(manifest["thinking"]["climax"])
    assert response.status_code == 200 and response.content == b"fake audio thinking-climax.ogg"
    assert client.get("/audio/../README.md").status_code == 404
