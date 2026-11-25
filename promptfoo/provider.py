"""promptfoo Python provider: runs one golden case through the agent, replayed from cassettes.

The prompt is the case id. The output is JSON with the final answer, the tool calls and the
end state, so promptfoo assertions can check behaviour, not only text. No API key needed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bank_agent.agent import run_episode  # noqa: E402
from bank_agent.providers import CassetteStore, ReplayProvider  # noqa: E402
from evals.dataset import load_cases  # noqa: E402

CASES = {case.id: case for case in load_cases()}
PROVIDER = ReplayProvider(CassetteStore())


def call_api(prompt: str, options: dict, context: dict) -> dict:
    case = CASES[prompt.strip()]
    version = (options.get("config") or {}).get("prompt_version", "v3")
    episode = asyncio.run(
        run_episode(
            PROVIDER,
            customer_id=case.customer,
            turns=case.turns,
            scenarios=case.scenarios,
            prompt_version=version,
            meta={"case_id": case.id},
        )
    )
    if episode.error:
        return {"error": episode.error}
    output = {
        "answer": episode.final_answer or "",
        "tools": [c.name for c in episode.tool_calls],
        "tool_calls": [{"name": c.name, "args": c.args} for c in episode.tool_calls],
        "citations": episode.citations,
        "transfers": len(episode.state["transfers"]),
        "cards": {card: v["status"] for card, v in episode.state["cards"].items()},
    }
    return {"output": json.dumps(output, ensure_ascii=False)}
