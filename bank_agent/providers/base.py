"""Provider-agnostic LLM interface.

The agent speaks a plain-text JSON action protocol, so any text-in/text-out model works.
Messages use three roles: "user" (the customer), "assistant" (the model's JSON actions)
and "tool" (tool results, with a "name").
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

Message = dict[str, str]


class ProviderError(RuntimeError):
    """The provider failed to return a completion (not a model mistake)."""


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_s: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("replayed")
        return data


class Provider(Protocol):
    name: str
    model: str

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        purpose: str = "agent",
        repeat: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


def request_key(purpose: str, system: str, messages: list[Message]) -> str:
    """Stable hash of a request: the cassette key. Any prompt, tool or data change gives a new key."""
    canonical = json.dumps(
        {"purpose": purpose, "system": system, "messages": messages},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_transcript(messages: list[Message]) -> str:
    """Render an agent conversation as one prompt, for providers without native multi-turn input."""
    parts = ["<conversation>"]
    for message in messages:
        role, content = message["role"], message["content"]
        if role == "user":
            parts.append(f"<customer>\n{content}\n</customer>")
        elif role == "assistant":
            parts.append(f"<assistant>\n{content}\n</assistant>")
        elif role == "tool":
            parts.append(f'<tool_result name="{message["name"]}">\n{content}\n</tool_result>')
        else:
            raise ValueError(f"Unknown role {role!r}")
    parts.append("</conversation>")
    parts.append("Reply with your next action: exactly one JSON object.")
    return "\n\n".join(parts)


def render_prompt(purpose: str, messages: list[Message]) -> str:
    if purpose == "agent":
        return render_transcript(messages)
    # Single-shot purposes (the judge) send one user message.
    return "\n\n".join(m["content"] for m in messages)
