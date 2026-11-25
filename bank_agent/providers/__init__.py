"""LLM providers: claude-cli (real runs without an API key), anthropic (API key) and replay (CI)."""

from __future__ import annotations

from pathlib import Path

from bank_agent.providers.base import LLMResponse, Message, Provider, ProviderError, request_key
from bank_agent.providers.cassette import (
    DEFAULT_CASSETTE_DIR,
    CassetteMiss,
    CassetteStore,
    RecordingProvider,
    ReplayProvider,
)

PROVIDERS = ("replay", "claude-cli", "anthropic")

__all__ = [
    "DEFAULT_CASSETTE_DIR",
    "PROVIDERS",
    "CassetteMiss",
    "CassetteStore",
    "LLMResponse",
    "Message",
    "Provider",
    "ProviderError",
    "RecordingProvider",
    "ReplayProvider",
    "make_provider",
    "request_key",
]


def make_provider(
    name: str,
    *,
    store: CassetteStore | None = None,
    label: str = "agent",
    model: str | None = None,
    effort: str | None = "low",
    record: bool = True,
    cassette_dir: Path = DEFAULT_CASSETTE_DIR,
) -> Provider:
    """Build a provider. Live providers are wrapped so every call is recorded for replay."""
    store = store or CassetteStore(cassette_dir)
    if name == "replay":
        return ReplayProvider(store)
    if name == "claude-cli":
        from bank_agent.providers.claude_cli import ClaudeCliProvider

        live: Provider = ClaudeCliProvider(model=model or "haiku", effort=effort)
    elif name == "anthropic":
        from bank_agent.providers.anthropic_api import AnthropicProvider

        live = AnthropicProvider(model=model, effort=effort)
    else:
        raise ValueError(f"Unknown provider {name!r}; choose from {', '.join(PROVIDERS)}")
    return RecordingProvider(live, store, label) if record else live
