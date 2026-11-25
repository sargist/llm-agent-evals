"""Threshold quality gates. A failed gate makes `python -m evals.run` exit with code 1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_GATES = Path(__file__).parent / "gates.yaml"


def _check(name: str, actual: float | int | None, op: str, threshold: float | int) -> dict[str, Any]:
    if actual is None:
        passed = False
    elif op == ">=":
        passed = actual >= threshold
    else:
        passed = actual <= threshold
    return {"name": name, "actual": actual, "op": op, "threshold": threshold, "passed": passed}


def evaluate_gates(summary: dict[str, Any], gates: dict[str, Any], judge_ran: bool) -> dict[str, Any]:
    checks = []
    if "min_pass_rate" in gates:
        checks.append(_check("pass_rate", summary["pass_rate"], ">=", gates["min_pass_rate"]))
    for suite, threshold in (gates.get("min_suite_pass_rate") or {}).items():
        if suite in summary["by_suite"]:
            checks.append(_check(f"suite.{suite}", summary["by_suite"][suite]["pass_rate"], ">=", threshold))
    for scorer, threshold in (gates.get("min_scorer_pass_rate") or {}).items():
        if scorer in summary["by_scorer"]:
            checks.append(
                _check(f"scorer.{scorer}", summary["by_scorer"][scorer]["pass_rate"], ">=", threshold)
            )
    if judge_ran and "min_judge_pass_rate" in gates:
        checks.append(
            _check("judge_pass_rate", summary["judge_pass_rate"], ">=", gates["min_judge_pass_rate"])
        )
    if "max_harness_errors" in gates:
        checks.append(_check("harness_errors", summary["harness_errors"], "<=", gates["max_harness_errors"]))
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def load_gates(path: Path = DEFAULT_GATES) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
