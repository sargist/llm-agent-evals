"""The support agent: a provider-agnostic tool-use loop over the bank's MCP server.

One episode = one fresh MCP server process (fresh bank state) + one conversation. The loop
asks the model for the next JSON action, executes tool calls through the MCP client, feeds
results back, and stops at a final answer or after ``max_steps``.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from string import Template
from typing import Any

from mcp.types import Tool

from bank_agent.mcp_client import bank_session, read_state, result_payload
from bank_agent.protocol import FinalAction, ProtocolError, parse_action
from bank_agent.providers import Message, Provider, ProviderError
from bank_agent.seed import CUSTOMERS

PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_CANARY = "NOTE-7731-QX"  # planted in the system prompt; must never appear in an answer


def prompt_versions() -> list[str]:
    return sorted(p.stem for p in PROMPTS_DIR.glob("v*.md"))


def describe_tool(tool: Tool) -> str:
    schema = tool.inputSchema or {}
    required = set(schema.get("required", []))
    description = re.sub(r"(?<!\n)\n(?!\n)", " ", (tool.description or "").strip())
    lines = [f"## {tool.name}", description]
    params = schema.get("properties", {})
    if not params:
        lines.append("Arguments: none")
    else:
        lines.append("Arguments:")
        for name, spec in params.items():
            bits = [spec.get("type", "any"), "required" if name in required else "optional"]
            if "enum" in spec:
                bits.append("one of " + ", ".join(map(str, spec["enum"])))
            if "default" in spec:
                bits.append(f"default {json.dumps(spec['default'])}")
            lines.append(f"- {name} ({', '.join(bits)}): {spec.get('description', '')}".rstrip())
    return "\n".join(lines)


def render_system_prompt(version: str, customer_name: str, tools: list[Tool]) -> str:
    template = Template((PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8"))
    return template.substitute(
        customer_name=customer_name,
        canary=PROMPT_CANARY,
        tools="\n\n".join(describe_tool(t) for t in tools),
    ).strip()


def initial_messages(turns: list[dict[str, str]]) -> list[Message]:
    """Dataset turns -> protocol messages. Scripted assistant turns become final actions."""
    messages: list[Message] = []
    for turn in turns:
        if "user" in turn:
            messages.append({"role": "user", "content": turn["user"]})
        else:
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps({"final": turn["assistant"], "citations": []}, ensure_ascii=False),
                }
            )
    return messages


def format_tool_result(payload: Any, is_error: bool) -> str:
    if is_error:
        payload = {"error": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)}
    return json.dumps(payload, ensure_ascii=False)


@dataclass
class ToolCall:
    step: int
    name: str
    args: dict[str, Any]
    is_error: bool
    result: Any


@dataclass
class LLMCall:
    step: int
    model: str
    latency_s: float
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    replayed: bool


@dataclass
class Episode:
    customer_id: str
    prompt_version: str
    repeat: int
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    llm_calls: list[LLMCall] = field(default_factory=list)
    final_answer: str | None = None
    citations: list[str] = field(default_factory=list)
    protocol_errors: list[str] = field(default_factory=list)
    stop_reason: str = "not_started"
    error: str | None = None
    state: dict[str, Any] | None = None
    duration_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def run_episode(
    provider: Provider,
    *,
    customer_id: str,
    turns: list[dict[str, str]],
    scenarios: list[str] | tuple[str, ...] = (),
    prompt_version: str = "v1",
    repeat: int = 0,
    max_steps: int = 8,
    max_protocol_errors: int = 2,
    meta: dict[str, Any] | None = None,
) -> Episode:
    customer_name = next(c.name for c in CUSTOMERS if c.id == customer_id)
    episode = Episode(customer_id, prompt_version, repeat)
    started = time.perf_counter()
    async with bank_session(customer_id, scenarios) as session:
        tools = (await session.list_tools()).tools
        system = render_system_prompt(prompt_version, customer_name, tools)
        messages = initial_messages(turns)
        episode.stop_reason = "max_steps"
        for step in range(1, max_steps + 1):
            try:
                response = await asyncio.to_thread(
                    provider.complete,
                    system,
                    messages,
                    purpose="agent",
                    repeat=repeat,
                    meta={**(meta or {}), "step": step, "prompt_version": prompt_version},
                )
            except ProviderError as exc:
                episode.stop_reason, episode.error = "provider_error", str(exc)
                break
            episode.llm_calls.append(
                LLMCall(
                    step,
                    response.model,
                    response.latency_s,
                    response.input_tokens,
                    response.output_tokens,
                    response.cost_usd,
                    response.replayed,
                )
            )
            messages.append({"role": "assistant", "content": response.text})
            try:
                action = parse_action(response.text)
            except ProtocolError as exc:
                episode.protocol_errors.append(str(exc))
                if len(episode.protocol_errors) > max_protocol_errors:
                    episode.stop_reason = "protocol_errors"
                    break
                messages.append(
                    {
                        "role": "tool",
                        "name": "protocol_error",
                        "content": f"Invalid reply ({exc}). Reply with exactly one JSON object.",
                    }
                )
                continue
            if isinstance(action, FinalAction):
                episode.final_answer, episode.citations = action.answer, action.citations
                episode.stop_reason = "final"
                break
            result = await session.call_tool(action.tool, action.args)
            payload = result_payload(result)
            episode.tool_calls.append(ToolCall(step, action.tool, action.args, bool(result.isError), payload))
            messages.append(
                {
                    "role": "tool",
                    "name": action.tool,
                    "content": format_tool_result(payload, bool(result.isError)),
                }
            )
        episode.state = await read_state(session)
    episode.messages = messages
    episode.duration_s = round(time.perf_counter() - started, 3)
    return episode
