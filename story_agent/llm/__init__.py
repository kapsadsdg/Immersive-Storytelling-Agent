"""LLM backends behind one small interface, chosen by configuration."""

from __future__ import annotations

from ..config import Settings
from .base import LLMClient, LLMError


def create_llm(settings: Settings) -> LLMClient:
    if settings.provider == "anthropic":
        from .anthropic_llm import AnthropicLLM

        return AnthropicLLM(settings.anthropic_model, settings.efforts, settings.anthropic_refusal_fallback,
                            api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url)
    if settings.provider == "openai":
        from .openai_llm import OpenAILLM

        return OpenAILLM(settings.openai_model, settings.openai_base_url, api_key=settings.openai_api_key)
    from .mock_llm import MockLLM

    return MockLLM(delay=0.3)


__all__ = ["LLMClient", "LLMError", "create_llm"]
