"""Claude Code CLI as a plain text-completion backend (`claude -p`).

Used for the real runs in this repo because it needs no API key. Each call runs from a
neutral temp directory with every customisation switched off: `--safe-mode` (no CLAUDE.md,
memory, hooks, skills, plugins or MCP servers), `--strict-mcp-config` without a config,
`--tools ""` (no built-in tools) and our own system prompt via `--system-prompt-file`.
Environment variables of a parent Claude Code session are not passed on.

Known limitation: with subscription (OAuth) login the CLI still adds a short context block
of its own to the first user turn (signed-in account e-mail, OS / working directory and
the current date, about 700 tokens). It cannot be disabled without `--bare`, which requires
an API key. Responses are scrubbed of e-mail addresses before they are stored in cassettes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from bank_agent.providers.base import LLMResponse, Message, ProviderError, render_prompt

STRIPPED_ENV_PREFIXES = ("CLAUDE", "AI_AGENT")


def find_claude_executable() -> str:
    explicit = os.environ.get("CLAUDE_CLI")
    if explicit:
        return explicit
    found = shutil.which("claude")
    if not found:
        raise ProviderError("Claude Code CLI not found on PATH (set CLAUDE_CLI to its path).")
    path = Path(found)
    if path.suffix.lower() in {".cmd", ".ps1", ""}:
        # npm shim on Windows: call the real binary so arguments are not re-parsed by cmd.exe.
        native = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if native.exists():
            return str(native)
    return found


def child_env() -> dict[str, str]:
    """Current environment minus variables of a parent Claude Code session (effort, session ids...)."""
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(STRIPPED_ENV_PREFIXES)}


class ClaudeCliProvider:
    name = "claude-cli"

    def __init__(
        self,
        model: str = "haiku",
        effort: str | None = "low",
        timeout_s: float = 180.0,
        executable: str | None = None,
        workdir: str | Path | None = None,
    ) -> None:
        self.model = model
        self.effort = effort
        self.timeout_s = timeout_s
        self.executable = executable or find_claude_executable()
        self.workdir = Path(
            workdir
            or os.environ.get("CLAUDE_CLI_WORKDIR")
            or Path(tempfile.gettempdir()) / "llm-agent-evals-cli"
        )
        self.workdir.mkdir(parents=True, exist_ok=True)

    def command(self, system_prompt_file: str) -> list[str]:
        cmd = [
            self.executable,
            "-p",
            "--model",
            self.model,
            "--output-format",
            "json",
            "--system-prompt-file",
            system_prompt_file,
            "--tools",
            "",
            "--strict-mcp-config",
            "--safe-mode",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    def complete(
        self,
        system: str,
        messages: list[Message],
        *,
        purpose: str = "agent",
        repeat: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> LLMResponse:
        prompt = render_prompt(purpose, messages)
        fd, system_file = tempfile.mkstemp(prefix="system-", suffix=".md", dir=self.workdir)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(system)
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                self.command(system_file),
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self.workdir,
                env=child_env(),
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"claude CLI timed out after {self.timeout_s}s") from exc
        finally:
            os.unlink(system_file)
        wall = time.perf_counter() - started
        if proc.returncode != 0:
            raise ProviderError(
                f"claude CLI exited with {proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}"
            )
        try:
            data = json.loads(proc.stdout)
        except ValueError as exc:
            raise ProviderError(f"claude CLI returned non-JSON output: {proc.stdout[:200]!r}") from exc
        if data.get("is_error") or data.get("subtype") != "success":
            raise ProviderError(f"claude CLI error: {data.get('subtype')} {data.get('api_error_status')}")
        usage = data.get("usage") or {}
        model_usage = data.get("modelUsage") or {}
        return LLMResponse(
            text=data.get("result") or "",
            model=next(iter(model_usage), self.model),
            latency_s=round(wall, 3),
            input_tokens=sum(
                int(usage.get(k) or 0)
                for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
            ),
            output_tokens=usage.get("output_tokens"),
            cost_usd=data.get("total_cost_usd"),
        )
