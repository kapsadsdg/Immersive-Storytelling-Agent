"""Provider-neutral LLM interface used by every agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)
DeltaCallback = Callable[[str], None]


class LLMError(RuntimeError):
    """The model call failed or returned something unusable."""


class LLMClient(ABC):
    """Two capabilities are enough for the whole agent:

    * ``structured`` - fill in a Pydantic schema (planning, decisions, state updates)
    * ``text``       - write free prose, optionally streamed chunk by chunk
    """

    name: str = "llm"

    @abstractmethod
    def structured(self, *, role: str, system: str, prompt: str, schema: type[T]) -> T: ...

    @abstractmethod
    def text(self, *, role: str, system: str, prompt: str, on_delta: DeltaCallback | None = None) -> str: ...
