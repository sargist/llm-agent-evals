"""Calibrate the LLM judge against human labels.

    python -m evals.calibrate --provider replay        # what CI runs
    python -m evals.calibrate --provider claude-cli    # grade new or changed items for real

Each item in evals/calibration/judge_calibration.yaml has a human pass/fail label per
rubric criterion. The positive class is "fail" (a defect), so TPR is the share of real
defects the judge catches and TNR the share of good replies it lets through. Items are
split into `dev` (may be looked at while tuning the rubric) and `holdout` (frozen: never
used for tuning, reported separately).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from bank_agent.providers import DEFAULT_CASSETTE_DIR, PROVIDERS, CassetteStore, make_provider
from evals.judge import CRITERIA, Judge, JudgeItem
from evals.stats import binary_rates, cohen_kappa

CALIBRATION_FILE = Path(__file__).parent / "calibration" / "judge_calibration.yaml"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
SPLITS = ("dev", "holdout")


def load_items(path: Path = CALIBRATION_FILE) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = data["items"]
    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate calibration item ids")
    for item in items:
        if item["split"] not in SPLITS:
            raise ValueError(f"{item['id']}: split must be one of {SPLITS}")
        if set(item["human"]) != set(CRITERIA) or not set(item["human"].values()) <= {"pass", "fail"}:
            raise ValueError(f"{item['id']}: human labels must give pass/fail for {CRITERIA}")
    return items


def to_judge_item(item: dict[str, Any]) -> JudgeItem:
    conversation = [
        {"role": "customer" if "customer" in t else "assistant", "content": next(iter(t.values()))}
        for t in item["conversation"]
    ]
    return JudgeItem(
        item["id"],
        item["lang"],
        conversation,
        item.get("tool_calls", []),
        item["answer"],
        item.get("notes", ""),
    )


def verdict_of(labels: dict[str, str]) -> str:
    return "fail" if "fail" in labels.values() else "pass"


def agreement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall and per-criterion agreement between human and judge labels."""
    graded = [r for r in rows if r["judge"]["verdict"] != "error"]
    if not graded:
        return {"n": 0}
    human = [verdict_of(r["human"]) for r in graded]
    judge = [r["judge"]["verdict"] for r in graded]
    out: dict[str, Any] = {
        "n": len(graded),
        "judge_errors": len(rows) - len(graded),
        "overall": {**binary_rates(human, judge), "kappa": cohen_kappa(human, judge)},
        "criteria": {},
    }
    for criterion in CRITERIA:
        h = [r["human"][criterion] for r in graded]
        j = [r["judge"]["criteria"][criterion] for r in graded]
        out["criteria"][criterion] = {**binary_rates(h, j), "kappa": cohen_kappa(h, j)}
    return out


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LLM judge calibration",
        "",
        f"Judge: `{report['judge_model']}` via `{report['provider']}`. Human labels: "
        f"{report['items']} items labelled by the repo author, positive class = fail (a defect).",
        "TPR = share of human-labelled defects the judge also flags; TNR = share of human-labelled "
        "passes the judge also passes. `n/a` = no item of that class in the split.",
        "",
    ]
    for split, stats in report["splits"].items():
        if not stats.get("n"):
            continue
        o = stats["overall"]
        lines += [
            f"## {split} (n={stats['n']}, judge errors {stats['judge_errors']})",
            "",
            "| criterion | human fails | accuracy | TPR | TNR | Cohen's kappa |",
            "|---|---|---|---|---|---|",
            f"| **overall verdict** | {o['positives']} | {_fmt(o['accuracy'])} | {_fmt(o['tpr'])} | "
            f"{_fmt(o['tnr'])} | {_fmt(o['kappa'])} |",
        ]
        for criterion, c in stats["criteria"].items():
            lines.append(
                f"| {criterion} | {c['positives']} | {_fmt(c['accuracy'])} | {_fmt(c['tpr'])} | "
                f"{_fmt(c['tnr'])} | {_fmt(c['kappa'])} |"
            )
        lines.append("")
    disagreements = [r for r in report["rows"] if r["judge"]["verdict"] != verdict_of(r["human"])]
    if disagreements:
        lines += ["## Disagreements", ""]
        for r in disagreements:
            human, judge, reason = verdict_of(r["human"]), r["judge"]["verdict"], r["judge"]["reason"]
            lines.append(f"- `{r['id']}` ({r['split']}): human {human}, judge {judge}. Judge: {reason}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m evals.calibrate", description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, default="replay")
    parser.add_argument("--model", help="judge model for live providers")
    parser.add_argument("--cassettes", type=Path, default=DEFAULT_CASSETTE_DIR)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    parser.add_argument("--min-holdout-kappa", type=float, help="exit 1 if holdout kappa is below this")
    args = parser.parse_args(argv)

    items = load_items()
    provider = make_provider(
        args.provider,
        store=CassetteStore(args.cassettes),
        label="judge-calibration",
        model=args.model,
        effort="medium",
    )
    judge = Judge(provider)
    rows = []
    for item in items:
        verdict = judge.grade(to_judge_item(item))
        rows.append(
            {"id": item["id"], "split": item["split"], "human": item["human"], "judge": verdict.to_dict()}
        )
        print(f"  {item['id']} ({item['split']}): human {verdict_of(item['human'])}, judge {verdict.verdict}")

    report = {
        "provider": args.provider,
        "judge_model": next(
            (r["judge"].get("model") for r in rows if r["judge"].get("model")), "claude-haiku-5-5"
        ),
        "items": len(items),
        "splits": {
            "all": agreement(rows),
            **{s: agreement([r for r in rows if r["split"] == s]) for s in SPLITS},
        },
        "rows": rows,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "judge-calibration.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    markdown = to_markdown(report)
    (args.out / "judge-calibration.md").write_text(markdown, encoding="utf-8")
    print()
    print(markdown)
    if any(r["judge"]["verdict"] == "error" and "cassette" in r["judge"]["reason"] for r in rows):
        return 2
    holdout_kappa = report["splits"]["holdout"].get("overall", {}).get("kappa")
    if args.min_holdout_kappa is not None and (
        holdout_kappa is None or holdout_kappa < args.min_holdout_kappa
    ):
        print(f"Holdout kappa {holdout_kappa} is below {args.min_holdout_kappa}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
