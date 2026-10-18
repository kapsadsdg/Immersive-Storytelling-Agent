"""Claude backend using the official Anthropic SDK.

* Structured calls use ``messages.parse`` with a Pydantic ``output_format``
  (constrained decoding, so the JSON always matches the schema).
* Prose is streamed so the reader sees the story as it is written.
* Reasoning depth is set per agent role through ``output_config.effort``.
* Optional server-side refusal fallback (``fallbacks="default"``) re-runs a
  declined request on a fallback model inside the same API call.
"""

from __future__ import annotations

import anthropic
import pydantic

from .base import DeltaCallback, LLMClient, LLMError, T

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicLLM(LLMClient):
    name = "anthropic"

    def __init__(self, model: str, efforts: dict[str, str], refusal_fallback: bool = True, *,
                 api_key: str | None = None, base_url: str | None = None, http_client=None):
        # api_key/base_url come from Settings (.env). When None, the SDK falls back to the
        # environment (ANTHROPIC_API_KEY or an `ant auth login` profile).
        # http_client is optional: inject one for proxies or for transport-level tests.
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url, max_retries=3, http_client=http_client)
        self.model = model
        self.efforts = efforts
        self.refusal_fallback = refusal_fallback

    def _request(self, role: str, system: str, prompt: str, max_tokens: int) -> dict:
        return {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": self.efforts.get(role, "medium")},
        }

    @staticmethod
    def _check_stop(response) -> None:
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMError(f"The model declined this request (category: {category}).")
        if response.stop_reason == "max_tokens":
            raise LLMError("The model ran out of output tokens before finishing.")

    def structured(self, *, role: str, system: str, prompt: str, schema: type[T]) -> T:
        kwargs = self._request(role, system, prompt, max_tokens=16000)
        try:
            if self.refusal_fallback:
                response = self.client.beta.messages.parse(
                    **kwargs, output_format=schema, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self.client.messages.parse(**kwargs, output_format=schema)
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except pydantic.ValidationError as exc:
            # The SDK parses before we can inspect stop_reason: an empty or cut-off reply
            # (refusal or max_tokens) surfaces here.
            raise LLMError(f"{role}: the model did not return usable structured output (it may have declined).") from exc

        self._check_stop(response)
        parsed = response.parsed_output
        if parsed is None:
            raise LLMError(f"{role}: the model returned no structured output.")
        return parsed

    def text(self, *, role: str, system: str, prompt: str, on_delta: DeltaCallback | None = None) -> str:
        kwargs = self._request(role, system, prompt, max_tokens=32000)
        if self.refusal_fallback:
            stream_ctx = self.client.beta.messages.stream(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
        else:
            stream_ctx = self.client.messages.stream(**kwargs)

        chunks: list[str] = []
        try:
            with stream_ctx as stream:
                for delta in stream.text_stream:
                    chunks.append(delta)
                    if on_delta:
                        on_delta(delta)
                final = stream.get_final_message()
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Could not reach the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc

        self._check_stop(final)
        text = "".join(block.text for block in final.content if block.type == "text")
        return text or "".join(chunks)
