"""Anthropic Messages API provider (needs ANTHROPIC_API_KEY and `pip install anthropic`)."""

from __future__ import annotations

import os
import time
from typing import Any

from bank_agent.providers.base import LLMResponse, Message, ProviderError

DEFAULT_MODEL = "claude-haiku-5-5"
# USD per million tokens (input, output) for prompts up to 100K tokens.
PRICES = {"claude-haiku-5-5": (0.10, 0.50)}


def to_api_messages(messages: list[Message]) -> list[dict[str, str]]:
    """Map our roles to the API's user/assistant turns. Tool results go back as user turns."""
    api: list[dict[str, str]] = []
    for message in messages:
        if message["role"] == "assistant":
            role, content = "assistant", message["content"]
        elif message["role"] == "tool":
            role, content = "user", f"TOOL_RESULT {message['name']}:\n{message['content']}"
        else:
            role, content = "user", message["content"]
        if api and api[-1]["role"] == role:
            api[-1]["content"] += "\n\n" + content
        else:
            api.append({"role": role, "content": content})
    return api


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self, model: str | None = None, effort: str | None = "low", max_tokens: int = 4096, client: Any = None
    ) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.effort = effort
        self.max_tokens = max_tokens
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ProviderError("pip install anthropic to use the anthropic provider") from exc
            client = anthropic.Anthropic()
        self.client = client

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        purpose: str = "agent",
        repeat: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": to_api_messages(messages),
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        started = time.perf_counter()
        try:
            response = self.client.messages.create(**kwargs)
        except Exception as exc:  # the SDK's typed errors all derive from Exception
            raise ProviderError(f"Anthropic API call failed: {type(exc).__name__}: {exc}") from exc
        latency = time.perf_counter() - started
        if response.stop_reason == "refusal":
            raise ProviderError("Model refused the request (stop_reason=refusal)")
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        price = PRICES.get(self.model)
        cost = (usage.input_tokens * price[0] + usage.output_tokens * price[1]) / 1e6 if price else None
        return LLMResponse(
            text=text,
            model=response.model,
            latency_s=round(latency, 3),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )
