"""LLM-as-judge with an explicit, per-criterion rubric.

The judge grades five criteria separately instead of giving one opaque score. That makes
it possible to calibrate each criterion against human labels (see evals/calibrate.py) and
to see which kinds of defect the judge is good or bad at catching.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bank_agent.agent import Episode
from bank_agent.protocol import ProtocolError, extract_json_object
from bank_agent.providers import Provider, ProviderError
from evals.dataset import Case

CRITERIA = ("correctness", "groundedness", "policy", "injection", "language")
LANGUAGE_NAMES = {"en": "English", "ru": "Russian", "hy": "Armenian"}
MAX_RESULT_CHARS = 1500

JUDGE_SYSTEM = (Path(__file__).parent / "prompts" / "judge.md").read_text(encoding="utf-8").strip()


@dataclass
class JudgeItem:
    id: str
    lang: str
    conversation: list[dict[str, str]]  # {"role": "customer" | "assistant", "content": ...}
    tool_calls: list[dict[str, Any]]  # {"tool", "args", "result"}
    answer: str
    notes: str = ""


@dataclass
class JudgeVerdict:
    verdict: str  # pass | fail | error
    criteria: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    latency_s: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def item_from_episode(case: Case, ep: Episode) -> JudgeItem:
    conversation = [
        {"role": "customer" if "user" in t else "assistant", "content": next(iter(t.values()))}
        for t in case.turns
    ]
    calls = [{"tool": c.name, "args": c.args, "result": c.result} for c in ep.tool_calls]
    return JudgeItem(
        case.id,
        case.expected_language,
        conversation,
        calls,
        ep.final_answer or "",
        case.expect.get("judge_notes", ""),
    )


def render_item(item: JudgeItem) -> str:
    convo = "\n".join(f"[{t['role']}] {t['content']}" for t in item.conversation)
    calls = []
    for n, call in enumerate(item.tool_calls, 1):
        result = json.dumps(call["result"], ensure_ascii=False)
        if len(result) > MAX_RESULT_CHARS:
            result = result[:MAX_RESULT_CHARS] + " ...(truncated)"
        calls.append(f"{n}. {call['tool']} {json.dumps(call['args'], ensure_ascii=False)}\n   -> {result}")
    return (
        f"<conversation>\n{convo}\n</conversation>\n\n"
        f"<tool_calls>\n{chr(10).join(calls) or '(none)'}\n</tool_calls>\n\n"
        f"<final_reply>\n{item.answer}\n</final_reply>\n\n"
        f"<reviewer_notes>\n{item.notes or '(none)'}\n</reviewer_notes>\n\n"
        f"<expected_language>{LANGUAGE_NAMES[item.lang]}</expected_language>\n\n"
        "Grade the final reply. Reply with the JSON object only."
    )


def parse_verdict(text: str) -> tuple[dict[str, str], str]:
    obj, _ = extract_json_object(text)
    if not isinstance(obj, dict):
        raise ProtocolError("judge reply is not a JSON object")
    criteria = {}
    for name in CRITERIA:
        value = str(obj.get(name, "")).strip().lower()
        if value not in ("pass", "fail"):
            raise ProtocolError(f"criterion {name} must be pass or fail, got {value!r}")
        criteria[name] = value
    return criteria, str(obj.get("reason", ""))


class Judge:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    def grade(self, item: JudgeItem, repeat: int = 0) -> JudgeVerdict:
        if not item.answer:
            return JudgeVerdict("error", reason="no final reply to grade")
        messages = [{"role": "user", "content": render_item(item)}]
        try:
            response = self.provider.complete(
                JUDGE_SYSTEM, messages, purpose="judge", repeat=repeat, meta={"item": item.id}
            )
        except ProviderError as exc:
            return JudgeVerdict("error", reason=f"provider error: {exc}")
        try:
            criteria, reason = parse_verdict(response.text)
        except ProtocolError as exc:
            return JudgeVerdict("error", reason=f"unparseable judge reply: {exc}", replayed=response.replayed)
        verdict = "fail" if "fail" in criteria.values() else "pass"
        return JudgeVerdict(
            verdict,
            criteria,
            reason,
            response.latency_s,
            response.input_tokens,
            response.output_tokens,
            response.cost_usd,
            response.replayed,
        )
