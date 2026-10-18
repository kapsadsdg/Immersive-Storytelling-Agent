"""Terminal rendering of **bold** key moments."""

from __future__ import annotations

import io

from rich.console import Console

from story_agent.cli import StreamPrinter, prose_text

BOLD_ON = "\x1b[1m"


def stream(chunks: list[str], terminal: bool) -> str:
    out = io.StringIO()
    console = (Console(file=out, force_terminal=True, width=200, color_system="standard") if terminal
               else Console(file=out, width=200))  # a plain file, as when output is piped
    printer = StreamPrinter(console)
    for chunk in chunks:
        printer.write(chunk)
    printer.finish()
    return out.getvalue()


# The markers arrive split across stream chunks, including between the two asterisks.
CHUNKS = ["You find *", "*the bra", "ss key*", "* under the stone. 3 * 4 = 12."]


def test_stream_renders_bold_in_a_terminal():
    text = stream(CHUNKS, terminal=True)
    assert "**" not in text
    assert BOLD_ON in text and "the brass key" in text.replace(BOLD_ON, "").replace("\x1b[0m", "")
    assert "3 * 4 = 12." in text  # a lone asterisk is printed as-is


def test_stream_drops_markers_when_piped():
    text = stream(CHUNKS, terminal=False)
    assert text.strip() == "You find the brass key under the stone. 3 * 4 = 12."


def test_bold_never_leaks_past_a_segment():
    out = io.StringIO()
    printer = StreamPrinter(Console(file=out, force_terminal=False, width=200))
    printer.write("An **unclosed phrase")
    printer.finish()
    assert printer.bold is False


def test_prose_text_marks_bold_spans():
    rendered = prose_text("Fog. **The bell** rings. Silence.")
    assert rendered.plain == "Fog. The bell rings. Silence."
    assert [(rendered.plain[s.start:s.end], str(s.style)) for s in rendered.spans] == [("The bell", "bold")]
