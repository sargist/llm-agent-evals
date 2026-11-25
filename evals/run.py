"""Run the eval suites against the agent and enforce the quality gates.

Examples:
    python -m evals.run --provider replay                       # what CI runs
    python -m evals.run --provider claude-cli --prompt v1        # real run, recorded to cassettes
    python -m evals.run --provider replay --tag k3 --repeat 3    # non-determinism subset

Exit codes: 0 = gates passed, 1 = a gate failed, 2 = harness errors (e.g. a cassette miss).
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bank_agent.agent import prompt_versions, run_episode
from bank_agent.providers import DEFAULT_CASSETTE_DIR, PROVIDERS, CassetteStore, Provider, make_provider
from evals.dataset import Case, load_cases
from evals.gates import DEFAULT_GATES, gate_results, load_gates
from evals.judge import Judge, item_from_episode
from evals.report import summarize, to_markdown, write_reports
from evals.scorers import score_episode

REPO_ROOT = Path(__file__).resolve().parent.parent


async def run_case(
    case: Case,
    repeat: int,
    provider: Provider,
    judge: Judge | None,
    prompt_version: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        episode = await run_episode(
            provider,
            customer_id=case.customer,
            turns=case.turns,
            scenarios=case.scenarios,
            prompt_version=prompt_version,
            repeat=repeat,
            meta={"case_id": case.id},
        )
        scores = score_episode(case, episode)
        verdict = None
        if judge and episode.final_answer:
            verdict = (
                await asyncio.to_thread(judge.grade, item_from_episode(case, episode), repeat)
            ).to_dict()
    passed = all(s.passed for s in scores.values())
    print(
        f"  {'PASS' if passed else 'FAIL'} {case.id} r{repeat}"
        + (f" judge={verdict['verdict']}" if verdict else ""),
        flush=True,
    )
    return {
        "case_id": case.id,
        "suite": case.suite,
        "lang": case.lang,
        "repeat": repeat,
        "passed": passed,
        "harness_error": episode.stop_reason == "provider_error",
        "scores": {name: s.to_dict() for name, s in scores.items()},
        "judge": verdict,
        "episode": episode.to_dict(),
    }


async def run_all(
    cases: list[Case],
    repeat: int,
    provider: Provider,
    judge: Judge | None,
    prompt_version: str,
    concurrency: int,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)
    jobs = [run_case(c, r, provider, judge, prompt_version, semaphore) for c in cases for r in range(repeat)]
    results = await asyncio.gather(*jobs)
    return sorted(results, key=lambda r: (r["suite"], r["case_id"], r["repeat"]))


def git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    versions = prompt_versions()
    p = argparse.ArgumentParser(
        prog="python -m evals.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--provider", choices=PROVIDERS, default="replay")
    p.add_argument("--model", help="model for live providers (claude-cli default: haiku)")
    p.add_argument("--prompt", choices=versions, default=versions[-1], help="system prompt version")
    p.add_argument("--suite", default="all", help="comma-separated suites, or 'all'")
    p.add_argument("--tag", help="only cases with this tag (comma-separated)")
    p.add_argument("--case", help="only these case ids (comma-separated)")
    p.add_argument("--repeat", type=int, default=1, help="runs per case, for pass^k and consistency")
    p.add_argument("--no-judge", action="store_true", help="skip the LLM judge")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--cassettes", type=Path, default=DEFAULT_CASSETTE_DIR)
    p.add_argument("--label", help="cassette file label for recording (default agent-<prompt>)")
    p.add_argument("--out", type=Path, help="report directory (default reports/<run id>)")
    p.add_argument("--gates", type=Path, default=DEFAULT_GATES)
    p.add_argument("--no-gates", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    cases = load_cases(
        suites=args.suite.split(","),
        tags=args.tag.split(",") if args.tag else None,
        ids=args.case.split(",") if args.case else None,
    )
    if not cases:
        print("No cases selected.")
        return 2
    store = CassetteStore(args.cassettes)
    provider = make_provider(
        args.provider, store=store, label=args.label or f"agent-{args.prompt}", model=args.model
    )
    judge = (
        None
        if args.no_judge
        else Judge(
            make_provider(
                args.provider, store=store, label=f"judge-{args.prompt}", model=args.model, effort="medium"
            )
        )
    )
    run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{args.provider}-{args.prompt}"
    print(
        f"Running {len(cases)} cases x {args.repeat} with {args.provider} (prompt {args.prompt})", flush=True
    )
    started = time.perf_counter()
    results = asyncio.run(run_all(cases, args.repeat, provider, judge, args.prompt, args.concurrency))
    run = {
        "id": run_id,
        "provider": args.provider,
        "model": next((c["model"] for r in results for c in r["episode"]["llm_calls"]), args.model or "n/a"),
        "prompt_version": args.prompt,
        "suites": sorted({c.suite for c in cases}),
        "repeat": args.repeat,
        "judge": judge is not None,
        "git_sha": git_sha(),
        "duration_s": round(time.perf_counter() - started, 1),
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    summary = summarize(results, run)
    report: dict[str, Any] = {"run": run, "summary": summary}
    if not args.no_gates:
        report["gates"] = gate_results(results, run, load_gates(args.gates), judge_ran=judge is not None)
    report["results"] = results
    out_dir = args.out or REPO_ROOT / "reports" / run_id
    write_reports(report, out_dir)
    print()
    print(to_markdown(report))
    print(f"Reports written to {out_dir}")
    if summary["harness_errors"]:
        return 2
    if "gates" in report and not report["gates"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
