"""Web interface: a FastAPI backend that streams the agent's work to a single-page UI.

Each story turn is streamed as newline-delimited JSON events
(status / decision / delta / reset / done / error), so the page can show which
agent is working and render prose as it is written.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import Settings
from ..engine import EngineEvent, StoryEngine
from ..llm import LLMClient, LLMError, create_llm
from ..models import Story
from ..pacing import assess
from ..storage import JSONStorage

log = logging.getLogger("story_agent.web")
STATIC = Path(__file__).parent / "static"
AUDIO_DIR = STATIC / "audio"
AUDIO_EXTENSIONS = (".mp3", ".ogg", ".m4a", ".wav")  # preference order when several exist
MUSIC_BANDS = {"calm": "thinking-calm", "tense": "thinking-tense", "climax": "thinking-climax", "default": "thinking"}


class NewStory(BaseModel):
    theme: str = Field(min_length=1, max_length=2000)


class PlayerTurn(BaseModel):
    input: str = Field(min_length=1, max_length=2000)


def story_view(story: Story) -> dict[str, Any]:
    data = story.model_dump(mode="json", exclude={"snapshots"})
    data["pacing_report"] = assess(story).render()
    data["can_revise"] = bool(story.snapshots)
    return data


def audio_manifest(audio_dir: Path) -> dict[str, Any]:
    """List the user's optional sound files so the page only requests files that exist."""
    found: dict[str, str] = {}
    if audio_dir.is_dir():
        files = {p.name.lower(): p.name for p in audio_dir.iterdir() if p.is_file()}
        for stem in ("click", *MUSIC_BANDS.values()):
            for ext in AUDIO_EXTENSIONS:
                if stem + ext in files:
                    found[stem] = f"/audio/{files[stem + ext]}"
                    break
    return {
        "click": found.get("click"),
        "thinking": {band: found.get(stem) for band, stem in MUSIC_BANDS.items()},
    }


def create_app(settings: Settings | None = None, llm: LLMClient | None = None,
               audio_dir: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    audio_dir = Path(audio_dir) if audio_dir else AUDIO_DIR
    audio_dir.mkdir(parents=True, exist_ok=True)
    storage = JSONStorage(settings.save_dir)
    engine = StoryEngine(llm or create_llm(settings), settings, storage)
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def lock_for(story_id: str) -> threading.Lock:
        with locks_guard:
            return locks.setdefault(story_id, threading.Lock())

    def stream(job: Callable[[Callable[[EngineEvent], None]], dict], release: Callable[[], None] | None = None):
        events: queue.Queue = queue.Queue()

        def worker() -> None:
            try:
                final = job(lambda e: events.put({"type": e.kind, **e.data}))
                events.put({"type": "done", **final})
            except LLMError as exc:
                events.put({"type": "error", "message": str(exc)})
            except Exception as exc:  # report, never hang the stream
                log.exception("story turn failed")
                events.put({"type": "error", "message": f"Unexpected error: {exc}"})
            finally:
                if release:
                    release()
                events.put(None)

        threading.Thread(target=worker, daemon=True).start()

        def body():
            while (item := events.get()) is not None:
                yield json.dumps(item, ensure_ascii=False) + "\n"

        return StreamingResponse(body(), media_type="application/x-ndjson")

    app = FastAPI(title="Immersive Storytelling Agent")
    app.state.lock_for = lock_for  # per-story locks, exposed for tests and diagnostics
    app.mount("/audio", StaticFiles(directory=audio_dir), name="audio")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    def config() -> dict:
        return {"provider": settings.provider, "model": settings.model_label}

    @app.get("/api/audio")
    def audio() -> dict:
        return audio_manifest(audio_dir)

    @app.get("/api/stories")
    def list_stories() -> list[dict]:
        return [s.__dict__ for s in storage.list()]

    @app.get("/api/stories/{story_id}")
    def get_story(story_id: str) -> dict:
        try:
            return story_view(storage.load(story_id))
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/stories")
    def new_story(body: NewStory):
        def job(on_event):
            story, result = engine.start(body.theme, on_event=on_event)
            return {"result": result.__dict__, "story": story_view(story)}

        return stream(job)

    @app.post("/api/stories/{story_id}/turns")
    def play_turn(story_id: str, body: PlayerTurn):
        try:
            story = storage.load(story_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc
        lock = lock_for(story_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "This story is already taking a turn.")

        def job(on_event):
            result = engine.play(story, body.input, on_event=on_event)
            return {"result": result.__dict__, "story": story_view(story)}

        return stream(job, release=lock.release)

    @app.delete("/api/stories/{story_id}", status_code=204)
    def delete_story(story_id: str) -> None:
        lock = lock_for(story_id)
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "This story is taking a turn. Try again when it finishes.")
        try:
            storage.delete(story_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc
        finally:
            lock.release()
            with locks_guard:
                locks.pop(story_id, None)

    return app


def main() -> None:
    import argparse
    import os

    import uvicorn

    parser = argparse.ArgumentParser(description="Immersive storytelling agent (web UI).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--provider", choices=["anthropic", "openai", "mock"], help="override LLM_PROVIDER")
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    args = parser.parse_args()
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider

    settings = Settings.from_env()
    print(f"Model: {settings.provider} / {settings.model_label}")
    for note in settings.diagnostics():
        print(f"! {note}")
    url = f"http://{args.host}:{args.port}"
    print(f"Open {url} in your browser. Press Ctrl+C to stop.")
    if args.open:
        import webbrowser

        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
