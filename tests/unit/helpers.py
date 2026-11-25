"""Builders for unit tests: cases and episodes without running a model or a server."""

from __future__ import annotations

from typing import Any

from bank_agent.agent import Episode, ToolCall
from bank_agent.seed import build_bank
from evals.dataset import Case


def make_case(
    suite: str = "tool_use", lang: str = "en", customer: str = "C003", user: str = "hi", **expect: Any
) -> Case:
    return Case(
        id="test-case", suite=suite, lang=lang, customer=customer, turns=[{"user": user}], expect=expect
    )


def make_episode(
    answer: str | None = "ok",
    calls: list[ToolCall] | None = None,
    citations: list[str] | None = None,
    state: dict[str, Any] | None = None,
    customer: str = "C003",
) -> Episode:
    calls = calls or []
    ep = Episode(customer_id=customer, prompt_version="v1", repeat=0)
    ep.tool_calls = calls
    ep.final_answer = answer
    ep.citations = citations or []
    ep.stop_reason = "final" if answer else "max_steps"
    ep.state = state if state is not None else build_bank().snapshot()
    ep.messages = [{"role": "tool", "name": c.name, "content": str(c.result)} for c in calls]
    return ep


def call(name: str, result: Any = None, is_error: bool = False, **args: Any) -> ToolCall:
    return ToolCall(
        step=1, name=name, args=args, is_error=is_error, result=result if result is not None else {}
    )
