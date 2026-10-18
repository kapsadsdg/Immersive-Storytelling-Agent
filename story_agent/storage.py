"""Save and load story sessions as JSON files (one file per story)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .models import Story

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class StorySummary:
    id: str
    title: str
    theme: str
    status: str
    turn: int
    updated_at: str


class JSONStorage:
    def __init__(self, directory: str | os.PathLike):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, story_id: str) -> Path:
        if not _SAFE_ID.match(story_id):
            raise ValueError(f"Invalid story id: {story_id!r}")
        return self.directory / f"{story_id}.json"

    def save(self, story: Story) -> Path:
        path = self._path(story.id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(story.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic on the same filesystem
        return path

    def load(self, story_id: str) -> Story:
        path = self._path(story_id)
        if not path.exists():
            raise FileNotFoundError(f"No saved story with id {story_id!r}")
        return Story.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, story_id: str) -> None:
        path = self._path(story_id)
        if not path.exists():
            raise FileNotFoundError(f"No saved story with id {story_id!r}")
        path.unlink()

    def list(self) -> list[StorySummary]:
        summaries = []
        for path in self.directory.glob("*.json"):
            try:
                story = Story.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue  # skip corrupt or foreign files
            summaries.append(
                StorySummary(story.id, story.world.title, story.theme, story.status, story.state.turn, story.updated_at)
            )
        return sorted(summaries, key=lambda s: s.updated_at, reverse=True)
