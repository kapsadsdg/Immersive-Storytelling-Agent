"""Configuration loading and the --check self-test, run without real keys."""

from __future__ import annotations

import io
import json

import httpx2
import pytest
from rich.console import Console

from story_agent import doctor
from story_agent.config import PROJECT_ROOT, Settings
from story_agent.llm.anthropic_llm import AnthropicLLM

VARS = ("LLM_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "SAVE_DIR")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in VARS:
        monkeypatch.delenv(name, raising=False)


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_env_file_key_is_stripped_and_beats_system_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "old-system-key")
    env = write(tmp_path / ".env", "ANTHROPIC_API_KEY= sk-ant-test-123  \nOPENAI_API_KEY=new-file-key\n")
    s = Settings.from_env(env, template_file=tmp_path / "missing")
    assert s.provider == "anthropic"
    assert s.anthropic_api_key == "sk-ant-test-123"
    assert s.openai_api_key == "new-file-key"
    assert s.key_source == "ANTHROPIC_API_KEY from .env"
    assert s.env_file == env


def test_empty_value_in_env_file_keeps_system_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "system-key")
    env = write(tmp_path / ".env", "ANTHROPIC_API_KEY=\nOPENAI_API_KEY=\n")
    s = Settings.from_env(env, template_file=tmp_path / "missing")
    assert s.provider == "openai" and s.openai_api_key == "system-key"
    assert s.key_source == "OPENAI_API_KEY from system environment"


def test_key_left_in_template_is_reported(tmp_path):
    template = write(tmp_path / ".env.example", "ANTHROPIC_API_KEY= sk-ant-in-template\n")
    s = Settings.from_env(tmp_path / ".env", template_file=template)  # no .env exists
    assert s.provider == "mock" and s.env_file is None
    notes = " ".join(s.diagnostics())
    assert ".env.example" in notes and "copy .env.example .env" in notes


def test_template_key_warning_when_env_exists(tmp_path):
    env = write(tmp_path / ".env", "ANTHROPIC_API_KEY=sk-ant-real\n")
    template = write(tmp_path / ".env.example", "ANTHROPIC_API_KEY=sk-ant-real\n")
    notes = " ".join(Settings.from_env(env, template_file=template).diagnostics())
    assert "clear it from the template" in notes


def test_non_default_anthropic_endpoint_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9999")
    env = write(tmp_path / ".env", "ANTHROPIC_API_KEY=sk-ant-x\n")
    s = Settings.from_env(env, template_file=tmp_path / "missing")
    assert any("127.0.0.1:9999" in n for n in s.diagnostics())
    env.write_text("ANTHROPIC_API_KEY=sk-ant-x\nANTHROPIC_BASE_URL=https://api.anthropic.com\n", encoding="utf-8")
    s = Settings.from_env(env, template_file=tmp_path / "missing")
    assert s.anthropic_base_url == "https://api.anthropic.com" and not s.diagnostics()


def test_keys_never_appear_in_repr(tmp_path):
    env = write(tmp_path / ".env", "ANTHROPIC_API_KEY=sk-ant-supersecret\n")
    assert "supersecret" not in repr(Settings.from_env(env, template_file=tmp_path / "missing"))


def test_relative_save_dir_is_inside_the_project(tmp_path):
    s = Settings.from_env(tmp_path / ".env", template_file=tmp_path / "missing")
    assert s.save_dir == PROJECT_ROOT / "saves"


# --------------------------------------------------------------------------- --check


def run_check_with(handler, tmp_path, monkeypatch) -> tuple[int, str]:
    env = write(tmp_path / ".env", "ANTHROPIC_API_KEY=sk-ant-test-key-0000\n")
    settings = Settings.from_env(env, template_file=tmp_path / "missing")
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(doctor, "create_llm", lambda s: AnthropicLLM(
        s.anthropic_model, s.efforts, False, api_key=s.anthropic_api_key, http_client=client))
    out = io.StringIO()
    code = doctor.run_check(settings, Console(file=out, width=200))
    return code, out.getvalue()


def test_check_reports_rejected_key(tmp_path, monkeypatch):
    def handler(request):
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                      "message": "invalid x-api-key"}})

    code, out = run_check_with(handler, tmp_path, monkeypatch)
    assert code == 1 and "API key" in out and "sk-ant-test-key-0000" not in out


def test_check_succeeds(tmp_path, monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert request.headers["x-api-key"] == "sk-ant-test-key-0000"
        events = [
            ("message_start", {"type": "message_start", "message": {
                "id": "m", "type": "message", "role": "assistant", "model": body["model"], "content": [],
                "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                               "usage": {"output_tokens": 1}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        data = "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events)
        return httpx2.Response(200, content=data.encode(), headers={"content-type": "text/event-stream"})

    code, out = run_check_with(handler, tmp_path, monkeypatch)
    assert code == 0 and "Connected" in out


def test_check_without_key_fails_clearly(tmp_path):
    settings = Settings.from_env(tmp_path / ".env", template_file=tmp_path / "missing")
    out = io.StringIO()
    assert doctor.run_check(settings, Console(file=out, width=200)) == 1
    assert "No API key" in out.getvalue()


@pytest.mark.parametrize("error, expected", [
    ("Anthropic API error 401: invalid x-api-key", "rejected"),
    ("OpenAI API error 429: You have no credits remaining", "credits"),
    ("Could not reach the Anthropic API: Connection error", "connect"),
    ("Anthropic API error 404: model: claude-x not found", "model"),
])
def test_explain(error, expected):
    assert expected in doctor.explain(error).lower()
