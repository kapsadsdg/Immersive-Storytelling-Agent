"""Terminal interface: play a story in the console with streamed prose."""

from __future__ import annotations

import argparse
import re
import shutil
import sys

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import AGENT_LABELS, Settings
from .context import render_events, render_outline, render_world
from .engine import EngineEvent, StoryEngine, TurnResult
from .llm import LLMError, create_llm
from .models import Story
from .scheduler import END_REQUEST
from .storage import JSONStorage

HELP = """\
Type what you do or say, or a number to pick a suggested choice.
You can also talk to the storyteller directly, e.g. "rewrite that scene so the guard is asleep"
or "from now on, make the story more of a romance".

Commands:
  /world          fixed world settings        /state     current state
  /cast [all]     characters (all = secrets)  /outline   plot outline
  /events         key event record            /log       decisions, agents run
  /revise <text>  ask for a revision          /end       ask the story to conclude
  /save           save now (auto-saved)       /quit      leave (progress is saved)
"""

AGENT_COLORS = {
    "world_architect": "magenta", "plot_planner": "magenta", "intent_interpreter": "cyan", "scene_director": "cyan",
    "character_agent": "bright_red", "narrator": "green", "narrative_critic": "red", "chronicler": "yellow",
}


_BOLD_SPAN = re.compile(r"\*\*([^*\n]+?)\*\*")


def prose_text(text: str) -> Text:
    """Turn a stored segment into Rich text, rendering **phrase** as bold."""
    out = Text()
    last = 0
    for match in _BOLD_SPAN.finditer(text):
        out.append(text[last:match.start()])
        out.append(match.group(1), style="bold")
        last = match.end()
    out.append(text[last:])
    return out


class StreamPrinter:
    """Prints streamed prose with word wrapping and **bold** key moments, and shows a
    spinner while agents work. A pair of asterisks toggles bold and is never printed."""

    def __init__(self, console: Console):
        self.console = console
        self.width = min(100, shutil.get_terminal_size((100, 20)).columns - 2)
        self.column = 0
        self.pending: list[tuple[str, bool]] = []  # the current word, as (text, bold) runs
        self.bold = False
        self.star = False  # saw one '*' and is waiting to see if a second follows
        self.status = None

    def _stop_spinner(self) -> None:
        if self.status is not None:
            self.status.stop()
            self.status = None

    def _out(self, text: str, bold: bool = False) -> None:
        self.console.print(text, end="", style="bold" if bold else None,
                           soft_wrap=True, markup=False, highlight=False, emoji=False)

    def _add(self, ch: str) -> None:
        if self.pending and self.pending[-1][1] == self.bold:
            self.pending[-1] = (self.pending[-1][0] + ch, self.bold)
        else:
            self.pending.append((ch, self.bold))

    def _emit_word(self) -> None:
        runs, self.pending = self.pending, []
        length = sum(len(t) for t, _ in runs)
        if self.column and self.column + length > self.width:
            self._out("\n")
            self.column = 0
            if runs:
                runs[0] = (runs[0][0].lstrip(), runs[0][1])
        for text, bold in runs:
            self._out(text, bold)
            self.column += len(text)

    def write(self, text: str) -> None:
        self._stop_spinner()
        for ch in text:
            if ch == "*":
                if self.star:  # "**" toggles bold
                    self.star = False
                    self.bold = not self.bold
                else:
                    self.star = True
                continue
            if self.star:  # a lone asterisk is ordinary text
                self.star = False
                self._add("*")
            if ch == "\n":
                self._emit_word()
                self._out("\n")
                self.column = 0
            elif ch == " ":
                self._add(" ")
                self._emit_word()
            else:
                self._add(ch)
                # CJK text has no spaces: flush every character so it still wraps.
                if ord(ch) > 0x2E80:
                    self._emit_word()

    def finish(self) -> None:
        if self.star:
            self.star = False
            self._add("*")
        self._emit_word()
        self.bold = False  # never let bold leak past the end of a segment
        if self.column:
            self._out("\n")
        self.column = 0
        self._stop_spinner()

    def __call__(self, event: EngineEvent) -> None:
        if event.kind == "status":
            self.finish()
            agent = event.data.get("agent", "")
            color = AGENT_COLORS.get(agent, "white")
            label = event.data.get("label") or agent
            self.status = self.console.status(f"[{color}]{escape(label)}[/]: {escape(event.data['message'])}...")
            self.status.start()
            if agent == "narrator":
                self.console.print()
        elif event.kind == "delta":
            self.write(event.data["text"])
        elif event.kind == "reset":
            self.finish()
            self.console.print("[dim italic](The Narrative Critic sent the scene back. Rewriting it...)[/]\n")
        elif event.kind == "decision":
            self.finish()
            note = f" [dim]({escape(event.data['policy_note'])})[/]" if event.data.get("policy_note") else ""
            self.console.print(
                f"[cyan]Decision[/] -> [bold]{event.data['action'].upper()}[/] "
                f"[dim]{escape(event.data['rationale'])}[/]{note}"
            )
        elif event.kind == "trace":
            self.finish()
            skipped = f"  [dim]skipped: {escape(', '.join(event.data['skipped']))}[/]" if event.data["skipped"] else ""
            self.console.print(f"[dim]Agents: {escape(', '.join(event.data['ran']))}[/]{skipped}")


class StoryCLI:
    def __init__(self, engine: StoryEngine, storage: JSONStorage, console: Console):
        self.engine = engine
        self.storage = storage
        self.console = console

    # ------------------------------------------------------------------ rendering

    def show_turn(self, result: TurnResult) -> None:
        for note in result.notes:
            self.console.print(f"[dim]* {escape(note)}[/]")
        if result.action == "clarify":
            self.console.print(Panel(escape(result.text), title="The storyteller asks", border_style="cyan"))
        if result.status == "concluded":
            self.console.print(Panel("[bold]THE END[/]", border_style="magenta", expand=False))
            return
        if result.choices:
            self.console.print()
            for i, choice in enumerate(result.choices, 1):
                self.console.print(f"  [bold cyan]{i}.[/] {escape(choice)}")
        self.console.print("[dim]  ...or type anything. /help for commands.[/]")

    def show_world(self, story: Story) -> None:
        self.console.print(Panel(escape(render_world(story.world)), title="World (fixed)", border_style="magenta"))

    def show_cast(self, story: Story, secrets: bool) -> None:
        table = Table(title="Cast", show_lines=True)
        for col in ("Name", "Role", "Description", "Motivation") + (("Secret",) if secrets else ()):
            table.add_column(col)
        p = story.protagonist
        table.add_row(p.name, "protagonist (you)", p.description, p.motivation, *([p.secret] if secrets else []))
        for c in story.characters:
            table.add_row(c.name, c.role, c.description, c.motivation, *([c.secret] if secrets else []))
        self.console.print(table)

    def show_state(self, story: Story) -> None:
        s = story.state
        table = Table(show_header=False, title=f"State after turn {s.turn}")
        table.add_column(style="bold")
        table.add_column()
        for key, value in [
            ("Location", s.location), ("Time", s.time), ("Situation", s.situation),
            ("You", s.protagonist_condition), ("Inventory", ", ".join(s.inventory) or "-"),
            ("Present", ", ".join(s.present_characters) or "-"), ("Tension", f"{s.tension}/10"),
            ("Open threads", "\n".join(s.open_threads) or "-"),
            ("Characters", "\n".join(f"{c.name}: {c.status} ({c.disposition})" for c in s.characters) or "-"),
            ("Story so far", s.summary or "-"),
        ]:
            table.add_row(key, escape(value))
        self.console.print(table)

    def show_log(self, story: Story) -> None:
        table = Table(title="Turn log: decisions and the agents that ran")
        for col in ("Turn", "Player", "Action", "Why", "Agents run"):
            table.add_column(col)
        for d in story.decisions[-12:]:
            why = d.rationale + (f" [{d.overridden_by_policy}]" if d.overridden_by_policy else "")
            ran = ", ".join(dict.fromkeys(AGENT_LABELS.get(s.agent, s.agent) for s in d.trace if s.ran)) or "-"
            action = d.action + (f"/{d.revision_scope}" if d.action == "revise" else "")
            table.add_row(str(d.turn), escape(d.user_input[:40]), action, escape(why), escape(ran))
        self.console.print(table)

    # ------------------------------------------------------------------ flow

    def new_story(self, theme: str | None) -> Story | None:
        while not theme:
            theme = self.console.input("[bold]What should the story be about?[/] ").strip()
        printer = StreamPrinter(self.console)
        story, result = self.engine.start(theme, on_event=printer)
        printer.finish()
        self.console.print()
        self.console.print(Panel(f"[bold]{escape(story.world.title)}[/]\n{escape(story.world.logline)}\n\n"
                                 f"[dim]You are {escape(story.protagonist.name)}. {escape(story.protagonist.motivation)}[/]",
                                 border_style="magenta", expand=False))
        self.show_turn(result)
        return story

    def pick_saved(self) -> Story | None:
        saves = [s for s in self.storage.list() if s.status != "concluded"]
        if not saves:
            return None
        self.console.print("[bold]Unfinished stories:[/]")
        for i, s in enumerate(saves[:9], 1):
            self.console.print(f"  [cyan]{i}.[/] {escape(s.title)} [dim](turn {s.turn}, {escape(s.theme[:40])})[/]")
        answer = self.console.input("Pick a number to continue, or press Enter for a new story: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= min(9, len(saves)):
            story = self.storage.load(saves[int(answer) - 1].id)
            self.console.print(Panel(prose_text(story.segments[-1].text), title=escape(story.world.title)))
            if story.pending_question:
                self.console.print(Panel(escape(story.pending_question), title="The storyteller asks"))
            self.show_turn(TurnResult(action="resume", text="", choices=story.choices, status=story.status))
            return story
        return None

    def run(self, theme: str | None = None, story_id: str | None = None) -> None:
        story = self.storage.load(story_id) if story_id else None
        if story is None and not theme:
            story = self.pick_saved()
        if story is None:
            story = self.new_story(theme)

        while story.status != "concluded":
            try:
                raw = self.console.input("\n[bold green]> [/]").strip()
            except (EOFError, KeyboardInterrupt):
                raw = "/quit"
            if not raw:
                continue
            if raw.startswith("/"):
                cmd, _, arg = raw.partition(" ")
                cmd = cmd.lower()
                if cmd in ("/quit", "/exit"):
                    self.storage.save(story)
                    self.console.print(f"[dim]Saved as {story.id}. Resume with: python -m story_agent --load {story.id}[/]")
                    return
                if cmd == "/help":
                    self.console.print(HELP)
                elif cmd == "/world":
                    self.show_world(story)
                elif cmd == "/cast":
                    self.show_cast(story, secrets=arg.strip() == "all")
                elif cmd == "/state":
                    self.show_state(story)
                elif cmd == "/outline":
                    self.console.print(Panel(escape(render_outline(story.outline)), title="Plot outline"))
                elif cmd == "/events":
                    self.console.print(Panel(escape(render_events(story.events, limit=50)), title="Key events"))
                elif cmd == "/log":
                    self.show_log(story)
                elif cmd == "/save":
                    path = self.storage.save(story)
                    self.console.print(f"[dim]Saved to {path}[/]")
                elif cmd == "/revise" and arg.strip():
                    self.take_turn(story, f"Please revise the story: {arg.strip()}")
                elif cmd == "/end":
                    self.take_turn(story, END_REQUEST)
                else:
                    self.console.print("[red]Unknown command.[/] /help lists them.")
                continue
            if raw.isdigit() and 1 <= int(raw) <= len(story.choices):
                raw = story.choices[int(raw) - 1]
                self.console.print(f"[dim]> {escape(raw)}[/]")
            self.take_turn(story, raw)
        self.console.print(f"[dim]The full story is saved in {self.storage.directory / (story.id + '.json')}[/]")

    def take_turn(self, story: Story, text: str) -> None:
        printer = StreamPrinter(self.console)
        try:
            result = self.engine.play(story, text, on_event=printer)
        except LLMError as exc:
            printer.finish()
            self.console.print(f"[red]The storyteller stumbled: {escape(str(exc))}[/] Try again.")
            return
        printer.finish()
        self.show_turn(result)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="story_agent", description="Immersive storytelling agent (terminal).")
    parser.add_argument("--theme", help="start a new story with this theme")
    parser.add_argument("--load", metavar="ID", help="continue a saved story")
    parser.add_argument("--provider", choices=["anthropic", "openai", "mock"], help="override LLM_PROVIDER")
    parser.add_argument("--check", action="store_true", help="check configuration and API access, then exit")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.provider:
        import os

        os.environ["LLM_PROVIDER"] = args.provider

    console = Console()
    if args.check:
        from .doctor import run_check

        sys.exit(run_check(console=console))
    settings = Settings.from_env()
    storage = JSONStorage(settings.save_dir)
    engine = StoryEngine(create_llm(settings), settings, storage)
    console.print(Panel.fit("[bold magenta]Immersive Storytelling Agent[/]\n"
                            f"[dim]model: {settings.provider} / {settings.model_label}[/]"))
    for note in settings.diagnostics():
        console.print(f"[yellow]! {escape(note)}[/]")
    try:
        StoryCLI(engine, storage, console).run(theme=args.theme, story_id=args.load)
    except LLMError as exc:
        console.print(f"[red]Could not start the story: {escape(str(exc))}[/]")
        sys.exit(1)
