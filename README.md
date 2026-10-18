# Immersive Storytelling Agent

An agentic interactive-fiction engine written in Python. Type a theme, and eight specialised LLM agents build a world with fixed rules, plan the plot, play the characters and tell the story with you, one decision at a time. A deterministic scheduler runs only the agents each turn needs.

It works with Claude through the Anthropic API, with OpenAI or any OpenAI-compatible local server such as Ollama, and with an offline mock for demos and tests. Optional music and click sounds can be added locally. None are included in the repository.

**License:** [MIT](LICENSE).

**Contents**

1. Setup and execution
2. Architecture
3. My interpretation of immersive storytelling
4. Design decisions and trade-offs
5. How AI-assisted development tools were used
6. External APIs, models, libraries, services and assets
7. Known limitations and next steps
8. Testing and verification
9. Contributing and license

---

## 1. Setup and execution

**Requirements:** Python 3.10 or newer, plus an API key for Anthropic or OpenAI, or a local OpenAI-compatible server.

### Quick start on Windows

1. Double-click `start.bat`. On the first run it creates `.venv`, installs the dependencies, copies `.env.example` to `.env`, and opens `.env` in Notepad.
2. Put your key in **`.env`**, not in `.env.example`, with no space after the equals sign, for example `ANTHROPIC_API_KEY=sk-ant-...`. Save the file.
3. Double-click `start.bat` again. It checks the configuration and API access, then opens http://127.0.0.1:8000.

On macOS or Linux, run `./start.sh` instead. It does the same three steps.

Always open the app at http://127.0.0.1:8000. If you open `index.html` directly as a file, the page cannot reach the server and shows a warning.

**Optional sound.** The repository ships no audio. To add music while the agents work, and a click sound, put your own files in `story_agent/web/static/audio/`. Use the names listed in the `README.txt` there, for example `thinking.mp3` and `click.mp3`, then reload the page. These files are ignored by git.

### Manual setup on any platform

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Then fill in one provider in `.env`.

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `anthropic`, `openai` or `mock`. If empty, it is picked from whichever API key is set. |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Claude credentials. The default model is `claude-opus-5-5`. |
| `ANTHROPIC_BASE_URL` | Optional. Leave empty for the official API. |
| `ANTHROPIC_REFUSAL_FALLBACK` | Re-runs a declined request on a fallback model inside the same call. On by default. |
| `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` | OpenAI credentials. The default model is `gpt-4.1`. Set the base URL to use Ollama, vLLM or LM Studio. |
| `EFFORT_<ROLE>` | Reasoning effort per agent on Claude, from `low` to `max`, for each of the eight roles, for example `EFFORT_SCENE_DIRECTOR`. The older `EFFORT_ARCHITECT` and `EFFORT_DIRECTOR` still work. |
| `SELF_CORRECTION` | Lets the Narrative Critic review key scenes and send a flawed one back to the Narrator once. |
| `STORY_LANGUAGE` | Language the story is written in. The default is `English`. Set `auto` to follow the language of the theme. |
| `SAVE_DIR` | Folder where stories are saved as JSON. |

`.env` is git-ignored, and no key, token or secret appears anywhere in the source code. Keys are read from `.env` at runtime. A non-empty value in `.env` takes priority over a system environment variable of the same name.

Check the setup at any time. The check never prints the full key:

```bash
python -m story_agent --check
```

### Running it

**Web interface (recommended):**

```bash
python -m story_agent.web
```

Open http://127.0.0.1:8000. Prose streams in as it is written, and a status line shows which agent is working. Side tabs show the World, Cast, State, Outline and key Events. The Agents tab shows each turn's decision and which agents ran or were skipped, and why. The start page lists saved stories, and each one can be continued or deleted.

**Terminal interface:**

```bash
python -m story_agent
python -m story_agent --theme "A heist in a city where memories are currency"
python -m story_agent --load <story-id>
```

Type an action or a line of dialogue, or a number to pick a suggested choice. Commands: `/world`, `/cast [all]`, `/state`, `/outline`, `/events`, `/log`, `/revise <text>`, `/end`, `/save`, `/quit`.

**No API key?** Add `--provider mock` to either command. The full agent loop runs with canned placeholder prose.

**Tests** run offline and need no key: `pytest`.

### How to steer the story

You never need special syntax. The Intent Interpreter reads intent:

- "I slip the letter into my coat and leave" continues the story.
- "Rewrite that scene so the guard is asleep" rolls back and rewrites the last scene.
- "From now on I want this to become a ghost story" rewrites the upcoming outline beats.
- "Let's end it here" writes the ending.
- Something ambiguous, or something the world forbids, gets one short clarifying question.


---

## 2. Architecture

The system has **eight logical agents** and **five deterministic core modules**. Not every player action calls all eight agents. A Scheduler triggers each agent only when the turn needs it, and records why each one ran or was skipped.

```
 STORY START   theme ──▶ World Architect ──▶ Plot Planner ──▶ State Manager builds the Story ──▶ scene(opening)

 EACH TURN     player input
                   │
                   ▼
               Scheduler ── picked a suggested choice, or the End command? ── yes ──┐
                   │ no                                                             │ (Intent Interpreter skipped)
                   ▼                                                                │
               Intent Interpreter: advance / clarify / revise / conclude ◀──────────┘
                   │
                   ▼
               Policy Guard (length limit, one question at a time, valid revisions)
                   │
     clarify ◀─────┼─────▶ revise: State Manager rolls back the last scene and/or Plot Planner re-plans
   (ask, stop)     ▼
             ┌─────────────────────────────── scene ────────────────────────────────┐
             │ Scene Director    plans what happens                    every scene  │
             │ Character Agent   plays the NPCs in the scene           if involved  │
             │ Narrator          writes the prose (streamed)           every scene  │
             │ Narrative Critic  checks the draft; may send it back    key moments  │
             │ Chronicler        records state, events, NPC memories   every scene  │
             │ Plot Planner      re-plans the remaining beats          major turns  │
             └──────────────────────────────────────────────────────────────────────┘
                   ▼
               auto-save, with the turn's dispatch trace
```

**The eight agents**

| Agent | Responsibility | Triggered |
|---|---|---|
| World Architect | Creates the world, its rules, the cast, their secrets and private knowledge, and the opening situation | At story creation |
| Intent Interpreter | Understands the player's action, dialogue or story-change request, and chooses advance, clarify, revise or conclude | Every turn, except a picked suggestion or the End command |
| Plot Planner | Plans the chapters, the central conflict and the key beats | At story creation, when the player redirects the plot, and when the Chronicler records a major turn |
| Character Agent | Plays the important NPCs from their own goals, knowledge, memories and relationships | Only when cast members take part in the planned scene (up to three per call) |
| Scene Director | Plans the scene from the player's action and the world state: outcome, events, who takes part, where it ends | Every scene |
| Narrator | Turns the scene plan and the character notes into prose | Every scene |
| Chronicler | Records the state, key events, what each character learned and remembers, relationships, and the long-term summary | After every scene |
| Narrative Critic | Checks causality, consistency, player agency and quality; can send a scene back once | Opening, ending, rewrites, turning points, tension 8 or more, and anomalous drafts |

**The five deterministic core modules**

| Module | File | Job |
|---|---|---|
| Scheduler | [scheduler.py](story_agent/scheduler.py) | Decides which agents run each turn, and writes the dispatch trace |
| Policy Guard | [policy.py](story_agent/policy.py) | Enforces hard rules on the Intent Interpreter's decision |
| Pacing Controller | [pacing.py](story_agent/pacing.py) | Measures progress against the outline and sets the tension target and the length limit |
| State Manager | [state_manager.py](story_agent/state_manager.py) | Commits every state change: story creation, beat replacement, Chronicler records, NPC memory merging and rollback snapshots |
| Context Builder | [context.py](story_agent/context.py) | Renders the story into the shared prompt text that every agent reads |

[engine.py](story_agent/engine.py) wires them together. Each agent lives in [story_agent/agents/](story_agent/agents/), with its prompt in [prompts.py](story_agent/prompts.py) and its output schema in [schemas.py](story_agent/schemas.py).

**Example dispatch.** Picking a suggested choice in a quiet scene runs three agents: the Scene Director, the Narrator and the Chronicler. Typing "I confront the Warden" runs six: the Intent Interpreter, the Scene Director, the Character Agent for the Warden, the Narrator, the Narrative Critic because it is a turning point, and the Chronicler. Creating a story runs seven.

**Data model.** These parts map directly onto the requirements:

- **Fixed world settings** live in `WorldBible`, a frozen model holding the genre, tone, setting, world rules, locations, themes, voice and language. Nothing can modify it after creation, and the Intent Interpreter is told it cannot be revised.
- **Current state tracking** lives in `StoryState`: location, time, situation, protagonist condition, inventory, who is present, open threads, tension, a rolling summary, and for each character their status, attitude, knowledge and memories.
- **The record of key events** lives in `KeyEvent`. Each entry has a turn number, a significance level and its consequences.
- **The adjustable plot outline** lives in `PlotOutline`: the central conflict and a list of beats marked done, active or pending. Re-plans replace only beats that are not done, and each one is logged.
- **Narrative pacing** lives in `PacingPlan` and the Pacing Controller.
- **History** keeps every segment, every turn's decision with its rationale and dispatch trace, and rollback snapshots.

---

## 3. My interpretation of immersive storytelling

Immersion is the feeling that **the world is real and that you matter in it**. In practice I took that to mean five things.

1. **Consistency.** 

The world rules are fixed. Consistency is what makes a fictional world feel solid.

2. **Consequences** 

Your action is always the first thing the next scene responds to. The Chronicler turns consequences into tracked state, such as items, injuries, attitudes and open threads, and into a key-event record that later scenes must respect.

3. **Agency over the story itself.** 

You are a co-author as well as a player. You can retcon a scene or redirect the plot, and the agent works out which you mean.

4. **Shape.** 

Stories have rhythm. A deliberate outline give the story rising tension, so it does not wander forever. The outline describes  users goals, not scripts.

5. **Atmosphere** 

Immersion is also sensory. The prose streams in as if it is being told aloud. One or two key moments per scene appear in bold, so turning points land with weight. Optional music plays while the story is being written, and it darkens as tension rises, so even the wait becomes part of the mood.

---

## 4. Design decisions and trade-offs

**Agent design**

- **Specialised rules** 
Building a world, planning a plot, reading intent, staging a scene, playing characters, writing prose, critiquing it and keeping records are different skills with different output shapes. Eight separate roles keep each prompt focused and let reasoning effort be tuned per role, for example low for the Intent Interpreter and medium for the Narrator. 
The cost is more calls than a single prompt, which is why the agents run on demand (next point).

- **On-demand dispatch** 
Most turns do not need every agent. The Scheduler skips the Intent Interpreter when the player picks a suggestion, calls the Character Agent only when cast members appear, saves the Narrative Critic for key moments and anomalies, and wakes the Plot Planner only at major turns. A typical turn therefore costs three to six model calls instead of eight. The rules are deterministic and every skip is logged with its reason, so the dispatch is predictable and testable. The trade-off is that a rule can miss a case a model would have caught. For example, a subtle inconsistency in an ordinary scene is not reviewed by the Critic.

- **Characters with their own minds** 
Each NPC keeps their own knowledge and memories, updated by the Chronicler and capped so prompts stay small. The Character Agent plays them only from what they know, which lets secrets and misunderstandings drive the plot. One call plays every NPC in the scene, up to three, rather than one call per character, which keeps the cost down at some loss of individual depth.

- **Judgment inside** 
The Intent Interpreter decides with structured output. The Policy Guard then enforces what models are bad at: counting turns, a hard length limit, never asking two clarifying questions in a row, and only allowing revisions that are possible. This keeps the loop from stalling or running forever while leaving the creative decisions to the model.

- **A code-organized loop** 
The engine runs a fixed pipeline, and the Scheduler switches its steps on or off. This is more predictable than letting a model call tools in an open loop. It is easier to test, works the same across providers, and gives each turn a known cost. 
The trade-off is less flexibility, because the agent cannot invent a new kind of step.

- **Structured outputs** 
Every non-prose call returns a validated Pydantic object. Claude uses constrained decoding, and OpenAI uses strict JSON schema. Local models fall back to JSON mode with one repair attempt. The schemas stay simple so they work on every backend, and values are clamped in code.

- **The Chronicler rewrites the whole state.** 
Asking for a full updated state is more robust than applying deltas, at the cost of a longer response. The State Manager still merges character records, so a character who leaves the scene keeps their knowledge and memories.

- **A Critic instead of a self-check** 
Previously the Chronicler also flagged continuity slips. That job now belongs to the Narrative Critic, which reviews before the scene is recorded. A flawed scene is therefore fixed before it enters the record, not after.

- **Snapshots for revision.** 
Before each scene the engine snapshots the state, the outline and the history lengths. Retconning a scene restores the snapshot and rewrites from there, so the state never contains facts from the rejected version.

- **Bounded context.** 
Agents receive the world bible, cast, current state, outline, the last 15 key events, the rolling summary, and only the last one or two scenes verbatim. Prompt size stays roughly constant however long the story runs.

**Presentation**

- **Plain prose with bold key moments.** 
Segments are plain text with one deliberate exception: the Narrator may mark one or two short phrases per scene as `**bold**`. 

- **Transparency versus immersion.** The status line names the working agent, and the Agents tab shows every turn's decision and dispatch. That openness is useful for a prototype, but it pulls the reader out of the fiction. As a compromise, the story column itself never shows machinery.

- **English by default.** Stories are written in English whatever language the theme is in. `STORY_LANGUAGE=auto` restores matching the theme's language.
---

## 5. How AI-assisted development tools were used

I built this prototype with **Claude Code**I worked with it in conversation: I gave it the brief, reviewed its plans before they were carried out, tested the result myself, and asked for changes.

**What Claude Code did**

- **Coding** It wrote the code, the tests and the first draft of this README.
- **SDK accuracy.** It checked the Anthropic and OpenAI SDK signatures against the installed packages instead of writing them from memory.
- **Verification.** It ran the test suite after every change and tested the provider adapters against fake HTTP transports. It drove the web page in a built-in browser against a scratch copy of the data.
- **Bugs found.** 

**Decisions I made or directed**

- **Structure.** I designed the brief into the first architecture, the four agents, the policy layer, the pacing controller, the snapshots and the provider abstraction.
- **Eight-agent architecture.** I redesigned the agent architecture into eight logical agents with on-demand triggers: World Architect, Intent Interpreter, Plot Planner, Character Agent, Scene Director, Narrator, Chronicler and Narrative Critic. I defined each agent's responsibility and when it should run. Claude Code implemented the design and proposed how to split the deterministic code into five core modules: Scheduler, Policy Guard, Pacing Controller, State Manager and Context Builder.
- **English only.** I decided the project and the stories should be in English by default.
- **Deleting stories.** I asked for saved stories to be deletable from the start page.
- **Sound.** I asked for music while the agents work and for click sounds. I chose to use my own audio files rather than generated or bundled sound, and to let the music follow the story's tension.
- **Typography.** I asked for typography that heightens key moments, and chose bold. I knowingly accepted bold markup in the saved text, which departs from the brief's plain-text rule. I dropped the other ideas, such as a line of exclamation marks before turning points.
- **Example themes.** I chose the example themes on the start page.

---

## 6. External APIs, models, libraries, services and assets

| Item | Use |
|---|---|
| Anthropic Messages API, `claude-opus-5-5` (default) | Story generation with structured outputs, streaming, per-role effort and refusal fallback |
| OpenAI Chat Completions API, `gpt-4.1` (default), or any OpenAI-compatible server | Alternative backend, including local models through Ollama, vLLM or LM Studio |
| Claude Code (Anthropic) | AI-assisted development; see section 5. Not needed to run the app |
| `anthropic` Python SDK 1.13 | Claude client |
| `openai` Python SDK 3.28 | OpenAI and compatible client |
| `pydantic` 2 | Domain model and LLM output schemas |
| `fastapi`, `uvicorn` | Web API and server |
| `rich` | Terminal interface |
| `python-dotenv` | Reads `.env` |
| `pytest`, `httpx`; `httpx2` (installed with the SDKs) | Tests and fake HTTP transports |
| Browser built-ins: Fetch, Streams and HTML audio | Streaming prose and optional sound in the web page |

The web page is plain HTML, CSS and JavaScript with no frameworks. It uses the reader's system serif font and loads no external fonts, images, scripts or CDNs. No audio ships with the repository. If you add sound files, make sure you have the rights to use them.

---

## 7. Known limitations and next steps

**Limitations**

- **Latency.** A turn takes three to six model calls depending on what the Scheduler triggers, and creating a story takes seven. That means noticeable waits on large models. Streaming and music soften the wait.
- **New architecture, mock only so far.** The real-model play described in section 8 used the earlier four-agent version. The eight-agent version is covered by automated tests and has been exercised on the offline mock, but not yet against a real model.
- **Rule-based triggers.** The Narrative Critic reviews only key moments and drafts that look anomalous, so a subtle inconsistency in an ordinary scene can slip through. The Character Agent plays at most three NPCs per scene.
- **Retcons.** Only the most recent scene can be retconned. Asking again rewrites that same scene. Earlier scenes can only be changed by steering forward.
- **Cast.** New minor characters are tracked in the state, but the core cast is fixed at creation.
- **Continuity.** Continuity checking is done by the model, so subtle contradictions can slip through.
- **Bold in saves.** Saved stories contain `**` markers, which other tools reading the JSON will see as raw asterisks.
- **Sound.** Sound exists only in the web interface, and only with sound files you add yourself.
- **Deletion.** Deleting a story is permanent.
- **Configuration.** Values in `.env` cannot be overridden from the command line.

**Next steps**

- **Real-model evaluation.** Run sessions that deliberately trigger clarify and revise, then tune prompts and effort per role.
- **Ending length.** Add a length target for endings.
- **Cost and latency.** Use prompt caching for the stable world-bible prefix.
- **Long stories.** Retrieve relevant past scenes through embeddings over the event record.
- **Player control.** Let players edit outline beats in the UI, branch a story to explore alternatives, and undo a deletion.
- **Export.** Export a finished story as a formatted e-book or PDF.
- **More atmosphere.** Add optional narration audio or scene illustrations.

---

## 8. Testing and verification

| Check | Result |
|---|---|
| Automated tests (`pytest`) | 82 passing, offline, no API key needed |
| Real play with Claude (`claude-opus-5-5`) through the Anthropic API, earlier four-agent version | Five saved stories, two of them played to a full ending in 4 and 6 turns |
| Eight-agent version on the offline mock | Every dispatch path exercised in both interfaces; older saved stories load and continue |
| Bold key moments in real play | About two bold phrases per scene, within the intended limit |

**What the tests cover**

- The agent loop: each decision type, both revision scopes, the one-question rule, the length limit and the Critic's rewrite.
- On-demand dispatch: every trigger rule of the Scheduler, the saved dispatch trace, and that the End command never becomes an in-story action.
- The State Manager: NPC knowledge and memory merging with limits, and beat replacement that keeps history.
- Persistence and deletion.
- The configuration and `--check` self-test.
- The web API, including the audio list and deletion.
- Prose cleanup and bold rendering in the terminal.

**Provider adapters.** These are tested at the HTTP level. Fake transports check the exact requests sent to Anthropic and OpenAI and parse correctly shaped responses. That covers structured output, streaming, refusals, the JSON-mode fallback for local servers, and quota errors.

**Web interface.** It was exercised in a browser on scratch data. The checks covered streaming, bold rendering, sound start and stop by tension band, the sound toggle, deletion, and a phone-width layout.

**Continuous integration.** GitHub Actions runs the linter and the full test suite on Windows and Ubuntu, with Python 3.10 and 3.12, on every push. The workflow is in `.github/workflows/tests.yml`.

---

## 9. Contributing and license

Contributions are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains the development setup, the checks to run before a pull request, and how to add or change an agent.

The project is released under the [MIT License](LICENSE).
