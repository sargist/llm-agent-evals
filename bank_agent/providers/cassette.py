"""Record and replay LLM calls (the VCR pattern).

A cassette record maps (request hash, repeat index) to the response the live model gave.
Replaying a run therefore reproduces it exactly: same tool calls, same answers, same scores.
CI uses replay only, so it needs no secrets and a prompt change without re-recording fails
loudly with a CassetteMiss instead of silently passing.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bank_agent.providers.base import LLMResponse, Message, Provider, ProviderError, request_key

DEFAULT_CASSETTE_DIR = Path(__file__).resolve().parents[2] / "evals" / "cassettes"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ALLOWED_EMAIL_SUFFIXES = (".example", "example.com", "example.org")
WINDOWS_USER_RE = re.compile(r"(?i)([a-z]:[\\/]+users[\\/]+)[^\\/\s\"']+")


class CassetteMiss(ProviderError):
    """No recorded response for this request: the prompt, tools or data changed since recording."""


def scrub(text: str) -> tuple[str, bool]:
    """Remove e-mail addresses and local user paths that a model might echo from its environment."""
    cleaned = EMAIL_RE.sub(
        lambda m: m.group(0) if m.group(0).lower().endswith(ALLOWED_EMAIL_SUFFIXES) else "<redacted-email>",
        text,
    )
    cleaned = WINDOWS_USER_RE.sub(r"\1<user>", cleaned)
    return cleaned, cleaned != text


class CassetteStore:
    def __init__(self, directory: Path = DEFAULT_CASSETTE_DIR) -> None:
        self.directory = Path(directory)
        self._records: dict[tuple[str, int], dict[str, Any]] = {}
        self._lock = threading.Lock()
        if self.directory.exists():
            for path in sorted(self.directory.glob("*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        record = json.loads(line)
                        self._records[(record["key"], record["repeat"])] = record

    def __len__(self) -> int:
        return len(self._records)

    def get(self, key: str, repeat: int) -> dict[str, Any] | None:
        return self._records.get((key, repeat))

    def put(self, label: str, record: dict[str, Any]) -> None:
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / f"{label}.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            self._records[(record["key"], record["repeat"])] = record


def _response_from(record: dict[str, Any]) -> LLMResponse:
    return LLMResponse(**record["response"], replayed=True)


class ReplayProvider:
    """Serves recorded responses only. Never calls a model."""

    name = "replay"

    def __init__(self, store: CassetteStore) -> None:
        self.store = store
        self.model = "replay"

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        purpose: str = "agent",
        repeat: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> LLMResponse:
        record = self.store.get(request_key(purpose, system, messages), repeat)
        if record is None:
            raise CassetteMiss(
                f"no cassette for purpose={purpose} repeat={repeat} meta={meta}; "
                "re-record with a live provider"
            )
        return _response_from(record)


class RecordingProvider:
    """Wraps a live provider. Reuses a recorded response when one exists, otherwise calls the
    model and appends the exchange to ``<cassette dir>/<label>.jsonl``."""

    def __init__(self, inner: Provider, store: CassetteStore, label: str, reuse: bool = True) -> None:
        self.inner = inner
        self.store = store
        self.label = label
        self.reuse = reuse
        self.name = inner.name
        self.model = inner.model

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        purpose: str = "agent",
        repeat: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> LLMResponse:
        key = request_key(purpose, system, messages)
        if self.reuse and (record := self.store.get(key, repeat)) is not None:
            return _response_from(record)
        response = self.inner.complete(system, messages, purpose=purpose, repeat=repeat, meta=meta)
        response.text, scrubbed = scrub(response.text)
        self.store.put(
            self.label,
            {
                "key": key,
                "repeat": repeat,
                "purpose": purpose,
                "provider": self.inner.name,
                "meta": meta or {},
                "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scrubbed": scrubbed,
                "request": {
                    "system_sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
                    "messages": messages,
                },
                "response": response.to_dict(),
            },
        )
        return response
