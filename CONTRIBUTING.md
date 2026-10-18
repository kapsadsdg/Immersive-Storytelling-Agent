# Contributing

Thanks for your interest in the Immersive Storytelling Agent. Issues and pull requests are welcome.

## Development setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
```

You do not need an API key to develop. The built-in mock model runs the whole agent loop offline:

```bash
python -m story_agent --provider mock
python -m story_agent.web --provider mock
```

## Before you open a pull request

```bash
python -m pyflakes story_agent tests
python -m pytest -q
```

Both run in CI on Windows and Ubuntu, with Python 3.10 and 3.12. Tests never call a real model, and they must not need network access or credentials.

## Where things live

| Path | What it holds |
|---|---|
| `story_agent/agents/` | The eight agents. Each one is a single prompt, schema and method |
| `story_agent/prompts.py` | Every agent's system prompt |
| `story_agent/schemas.py` | The structured outputs the agents fill in |
| `story_agent/scheduler.py` | When each agent runs, and the dispatch trace |
| `story_agent/policy.py`, `pacing.py`, `state_manager.py`, `context.py` | The other deterministic core modules |
| `story_agent/engine.py` | The orchestrator that wires agents and modules together |
| `story_agent/llm/` | Provider adapters and the offline mock |
| `story_agent/cli.py`, `story_agent/web/` | The terminal and browser interfaces |

## Adding or changing an agent

1. Add the system prompt to `prompts.py` and the output schema to `schemas.py`. Keep schemas simple: every field required, no dicts, no numeric constraints. Clamp values in code.
2. Add the agent class in `story_agent/agents/` and its key and label to `AGENT_LABELS` in `config.py`.
3. Decide when it runs. Put that rule in `scheduler.py`, call the agent from `engine.py`, and record it in the turn's trace.
4. Teach `MockLLM` to answer the new schema, so the agent can run offline.
5. Add tests for the trigger rule and for the state changes it causes.

## Secrets

Never commit `.env`, API keys or real saved stories. `.env` and `saves/*.json` are already ignored by git.
