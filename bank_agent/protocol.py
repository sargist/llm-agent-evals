"""The JSON action protocol between the agent loop and the model.

Each model reply must be one JSON object, either
    {"tool": "<name>", "args": {...}}                      call a tool, or
    {"final": "<answer>", "citations": ["faq-001", ...]}   answer the customer.
Models sometimes wrap JSON in code fences or add a sentence around it; the parser accepts
that (and reports it), but rejects anything ambiguous.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


class ProtocolError(ValueError):
    """The reply is not a valid action."""


@dataclass(frozen=True)
class ToolAction:
    tool: str
    args: dict[str, Any]
    extra_text: bool = False


@dataclass(frozen=True)
class FinalAction:
    answer: str
    citations: list[str] = field(default_factory=list)
    extra_text: bool = False


def extract_json_object(text: str) -> tuple[Any, bool]:
    """The first JSON value in the text, and whether anything (fences, prose) surrounded it."""
    stripped = text.strip()
    fenced = FENCE_RE.match(stripped)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped), fenced is not None
    except ValueError:
        pass
    start = stripped.find("{")
    if start < 0:
        raise ProtocolError("no JSON object found")
    try:
        obj, _ = json.JSONDecoder().raw_decode(stripped[start:])
    except ValueError as exc:
        raise ProtocolError(f"invalid JSON: {exc.msg}") from None
    return obj, True


def parse_action(text: str) -> ToolAction | FinalAction:
    obj, extra = extract_json_object(text)
    if not isinstance(obj, dict):
        raise ProtocolError("the reply must be a JSON object")
    has_tool, has_final = "tool" in obj, "final" in obj
    if has_tool == has_final:
        raise ProtocolError('the object must contain exactly one of "tool" or "final"')
    if has_tool:
        tool, args = obj["tool"], obj.get("args", {})
        if not isinstance(tool, str) or not tool.strip():
            raise ProtocolError('"tool" must be a non-empty string')
        if not isinstance(args, dict):
            raise ProtocolError('"args" must be a JSON object')
        return ToolAction(tool.strip(), args, extra)
    answer, citations = obj["final"], obj.get("citations", [])
    if not isinstance(answer, str) or not answer.strip():
        raise ProtocolError('"final" must be a non-empty string')
    if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
        raise ProtocolError('"citations" must be a list of strings')
    return FinalAction(answer.strip(), [c.strip() for c in citations], extra)
