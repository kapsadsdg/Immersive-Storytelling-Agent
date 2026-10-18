"""Startup self-check: `python -m story_agent --check`.

Verifies dependencies and configuration, then makes one tiny API call and
explains any failure in plain language.
"""

from __future__ import annotations

import importlib.util
import sys

from rich.console import Console
from rich.markup import escape

from .config import Settings, mask
from .llm import LLMError, create_llm

REQUIRED = ("anthropic", "openai", "pydantic", "dotenv", "rich", "fastapi", "uvicorn")


def explain(error: str) -> str:
    """Turn a provider error into a hint the player can act on."""
    low = error.lower()
    if "401" in low or "authentication" in low or "invalid x-api-key" in low or "incorrect api key" in low:
        return "The API key was rejected as invalid or revoked. Check the key in .env."
    if "403" in low or "permission" in low:
        return "This key does not have access to this model."
    if "429" in low or "quota" in low or "credit" in low or "rate limit" in low:
        return "Out of credits or rate-limited. Check your account balance."
    if "404" in low or "not_found" in low or ("model" in low and "not found" in low):
        return "Unknown model name. Check ANTHROPIC_MODEL / OPENAI_MODEL."
    if "could not reach" in low or "connect" in low or "timed out" in low:
        return "Could not connect. Check your network, proxy, and ANTHROPIC_BASE_URL / OPENAI_BASE_URL."
    return "See the error message above."


def run_check(settings: Settings | None = None, console: Console | None = None) -> int:
    console = console or Console()
    ok = True

    missing = [name for name in REQUIRED if importlib.util.find_spec(name) is None]
    if missing:
        console.print(f"[red]Missing packages: {', '.join(missing)}[/]")
        console.print(f"  Interpreter: {escape(sys.executable)}")
        console.print("  Run: .venv\\Scripts\\python -m pip install -r requirements.txt")
        return 1

    settings = settings or Settings.from_env()
    console.print(f"Provider: [bold]{settings.provider}[/]   Model: [bold]{escape(settings.model_label)}[/]")
    console.print(f"Config file: {escape(str(settings.env_file) if settings.env_file else '(no .env found)')}")
    if settings.provider != "mock":
        console.print(f"Key: {escape(mask(settings.active_key))}   [dim]{escape(settings.key_source)}[/]")
    for note in settings.diagnostics():
        console.print(f"[yellow]! {escape(note)}[/]")

    if settings.provider == "mock":
        console.print("[red]No API key available, so no real model can be tested.[/]")
        return 1

    with console.status("Calling the model once..."):
        try:
            llm = create_llm(settings)
            reply = llm.text(role="intent_interpreter", system="You are a connectivity check.", prompt="Reply with the word OK.")
        except LLMError as exc:
            ok = False
            console.print(f"[red]Call failed:[/] {escape(str(exc))}")
            console.print(f"[yellow]-> {escape(explain(str(exc)))}[/]")
        except Exception as exc:  # e.g. the SDK refusing to build a client without credentials
            ok = False
            console.print(f"[red]Call failed:[/] {escape(type(exc).__name__)}: {escape(str(exc))}")
            console.print(f"[yellow]-> {escape(explain(str(exc)))}[/]")
    if ok:
        console.print(f"[green]Connected.[/] Model replied: {escape(reply.strip()[:40])}")
    return 0 if ok else 1
