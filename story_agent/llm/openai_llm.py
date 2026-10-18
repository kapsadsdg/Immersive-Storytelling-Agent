"""OpenAI backend, also usable with any OpenAI-compatible server (Ollama, vLLM, LM Studio).

Structured calls try strict JSON-schema decoding first. Servers that do not
support it fall back to JSON mode with the schema in the prompt, validated by
Pydantic with one repair attempt.
"""

from __future__ import annotations

import json

import openai
from pydantic import ValidationError

from .base import DeltaCallback, LLMClient, LLMError, T


class OpenAILLM(LLMClient):
    name = "openai"

    def __init__(self, model: str, base_url: str | None = None, *, api_key: str | None = None, http_client=None):
        # api_key comes from Settings (.env); when None the SDK reads OPENAI_API_KEY.
        # http_client is optional: inject one for proxies or for transport-level tests.
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=3, http_client=http_client)
        self.model = model
        self._strict_supported = True

    @staticmethod
    def _messages(system: str, prompt: str) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

    def structured(self, *, role: str, system: str, prompt: str, schema: type[T]) -> T:
        if self._strict_supported:
            try:
                completion = self.client.chat.completions.parse(
                    model=self.model, messages=self._messages(system, prompt), response_format=schema
                )
                message = completion.choices[0].message
                if getattr(message, "refusal", None):
                    raise LLMError(f"The model declined this request: {message.refusal}")
                if message.parsed is not None:
                    return message.parsed
            except openai.BadRequestError:
                # Typically a local server without json_schema support: use JSON mode from now on.
                self._strict_supported = False
            except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError, ValidationError) as exc:
                raise LLMError(f"{role}: the model did not return usable structured output ({type(exc).__name__}).") from exc
            except openai.APIConnectionError as exc:
                raise LLMError(f"Could not reach the OpenAI API: {exc}") from exc
            except openai.APIStatusError as exc:
                raise LLMError(f"OpenAI API error {exc.status_code}: {exc.message}") from exc
        return self._json_mode(system, prompt, schema)

    def _json_mode(self, system: str, prompt: str, schema: type[T]) -> T:
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        messages = self._messages(
            system + "\n\nReply with a single JSON object that matches this JSON schema exactly:\n" + schema_text,
            prompt,
        )
        last_error = ""
        for _ in range(2):
            try:
                completion = self.client.chat.completions.create(
                    model=self.model, messages=messages, response_format={"type": "json_object"}
                )
            except openai.APIError as exc:
                raise LLMError(f"OpenAI-compatible API error: {exc}") from exc
            raw = completion.choices[0].message.content or ""
            try:
                return schema.model_validate_json(raw)
            except ValidationError as exc:
                last_error = str(exc)
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That JSON was invalid:\n{last_error}\nReturn corrected JSON only."},
                ]
        raise LLMError(f"Model did not return valid JSON for {schema.__name__}: {last_error[:300]}")

    def text(self, *, role: str, system: str, prompt: str, on_delta: DeltaCallback | None = None) -> str:
        chunks: list[str] = []
        try:
            stream = self.client.chat.completions.create(
                model=self.model, messages=self._messages(system, prompt), stream=True
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    chunks.append(delta)
                    if on_delta:
                        on_delta(delta)
        except openai.APIConnectionError as exc:
            raise LLMError(f"Could not reach the OpenAI API: {exc}") from exc
        except openai.APIStatusError as exc:
            raise LLMError(f"OpenAI API error {exc.status_code}: {exc.message}") from exc
        return "".join(chunks)
