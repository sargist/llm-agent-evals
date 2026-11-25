"""Summaries and reports (JSON, Markdown, HTML) for an eval run."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.judge import CRITERIA
from evals.stats import consistency, pass_at_1, pass_hat_k, percentile

SUITE_ORDER = ("tool_use", "safety", "injection", "rag")


def _rate(passed: int, total: int) -> float | None:
    return round(passed / total, 3) if total else None


def _group(results: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        groups[r[key]].append(r)
    out = {}
    for name in sorted(groups, key=lambda s: (SUITE_ORDER.index(s) if s in SUITE_ORDER else 99, s)):
        rows = groups[name]
        judged = [r for r in rows if r["judge"] and r["judge"]["verdict"] in ("pass", "fail")]
        out[name] = {
            "runs": len(rows),
            "passed": sum(r["passed"] for r in rows),
            "pass_rate": _rate(sum(r["passed"] for r in rows), len(rows)),
            "judge_pass_rate": _rate(sum(r["judge"]["verdict"] == "pass" for r in judged), len(judged)),
        }
    return out


def summarize(results: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    by_scorer: dict[str, dict[str, int]] = defaultdict(lambda: {"applicable": 0, "passed": 0})
    for r in results:
        for name, score in r["scores"].items():
            by_scorer[name]["applicable"] += 1
            by_scorer[name]["passed"] += score["passed"]
    judged = [r for r in results if r["judge"] and r["judge"]["verdict"] in ("pass", "fail")]
    criteria_fails = {c: sum(r["judge"]["criteria"].get(c) == "fail" for r in judged) for c in CRITERIA}

    calls = [c for r in results for c in r["episode"]["llm_calls"]]
    judge_calls = [r["judge"] for r in results if r["judge"] and r["judge"].get("latency_s") is not None]
    run_latency = [sum(c["latency_s"] for c in r["episode"]["llm_calls"]) for r in results]
    tokens_in = sum(c["input_tokens"] or 0 for c in calls) + sum(j["input_tokens"] or 0 for j in judge_calls)
    tokens_out = sum(c["output_tokens"] or 0 for c in calls) + sum(
        j["output_tokens"] or 0 for j in judge_calls
    )
    cost = sum(c["cost_usd"] or 0 for c in calls) + sum(j["cost_usd"] or 0 for j in judge_calls)

    runs_by_case: dict[str, list[bool]] = defaultdict(list)
    tool_seqs: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for r in sorted(results, key=lambda r: (r["case_id"], r["repeat"])):
        runs_by_case[r["case_id"]].append(r["passed"])
        tool_seqs[r["case_id"]].add(tuple(c["name"] for c in r["episode"]["tool_calls"]))

    summary: dict[str, Any] = {
        "cases": len(runs_by_case),
        "runs": len(results),
        "passed": sum(r["passed"] for r in results),
        "pass_rate": _rate(sum(r["passed"] for r in results), len(results)),
        "judge_pass_rate": _rate(sum(r["judge"]["verdict"] == "pass" for r in judged), len(judged)),
        "judge_errors": sum(1 for r in results if r["judge"] and r["judge"]["verdict"] == "error"),
        "judge_criteria_failures": criteria_fails if judged else {},
        "harness_errors": sum(r["harness_error"] for r in results),
        "by_suite": _group(results, "suite"),
        "by_language": _group(results, "lang"),
        "by_scorer": {
            k: {**v, "pass_rate": _rate(v["passed"], v["applicable"])} for k, v in sorted(by_scorer.items())
        },
        "llm_calls": {
            "agent": len(calls),
            "judge": len(judge_calls),
            "live": sum(not c["replayed"] for c in calls) + sum(not j["replayed"] for j in judge_calls),
        },
        "latency_s": {
            "per_call_p50": percentile([c["latency_s"] for c in calls], 50),
            "per_call_p95": percentile([c["latency_s"] for c in calls], 95),
            "per_case_p50": percentile(run_latency, 50),
            "per_case_p95": percentile(run_latency, 95),
        },
        "tokens": {"input": tokens_in, "output": tokens_out},
        "cost_usd_list_price": round(cost, 4),
    }
    if run.get("repeat", 1) > 1:
        summary["repeats"] = {
            "k": run["repeat"],
            "pass_at_1": pass_at_1(runs_by_case),
            "pass_hat_k": pass_hat_k(runs_by_case),
            "outcome_consistency": consistency(runs_by_case),
            "tool_sequence_consistency": round(
                sum(len(s) == 1 for s in tool_seqs.values()) / len(tool_seqs), 3
            ),
        }
    summary["failures"] = [
        {
            "case_id": r["case_id"],
            "repeat": r["repeat"],
            "suite": r["suite"],
            "lang": r["lang"],
            "failed": {n: s["details"] for n, s in r["scores"].items() if not s["passed"]},
            "judge": r["judge"]["reason"] if r["judge"] and r["judge"]["verdict"] == "fail" else None,
        }
        for r in results
        if not r["passed"] or (r["judge"] and r["judge"]["verdict"] == "fail")
    ]
    return summary


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def to_markdown(report: dict[str, Any]) -> str:
    run, s = report["run"], report["summary"]
    lines = [
        f"# Eval run `{run['id']}`",
        "",
        f"- provider: `{run['provider']}`, model: `{run['model']}`, prompt: `{run['prompt_version']}`, "
        f"repeat: {run['repeat']}, judge: {'on' if run['judge'] else 'off'}",
        f"- cases: {s['cases']}, runs: {s['runs']}, deterministic pass rate: **{_pct(s['pass_rate'])}**, "
        f"judge pass rate: **{_pct(s['judge_pass_rate'])}**",
        f"- LLM calls: {s['llm_calls']['agent']} agent + {s['llm_calls']['judge']} judge "
        f"({s['llm_calls']['live']} live in this run), harness errors: {s['harness_errors']}",
        f"- latency per case p50/p95: {s['latency_s']['per_case_p50']}s / {s['latency_s']['per_case_p95']}s, "
        f"tokens in/out: {s['tokens']['input']}/{s['tokens']['output']}, "
        f"cost at list price: ${s['cost_usd_list_price']}",
        "",
        "## By suite",
        "",
        "| suite | runs | pass rate | judge pass rate |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {k} | {v['runs']} | {_pct(v['pass_rate'])} | {_pct(v['judge_pass_rate'])} |"
        for k, v in s["by_suite"].items()
    ]
    lines += [
        "",
        "## By language",
        "",
        "| language | runs | pass rate | judge pass rate |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {k} | {v['runs']} | {_pct(v['pass_rate'])} | {_pct(v['judge_pass_rate'])} |"
        for k, v in s["by_language"].items()
    ]
    lines += ["", "## By scorer", "", "| scorer | applicable | passed | pass rate |", "|---|---|---|---|"]
    lines += [
        f"| {k} | {v['applicable']} | {v['passed']} | {_pct(v['pass_rate'])} |"
        for k, v in s["by_scorer"].items()
    ]
    if s.get("judge_criteria_failures"):
        lines += [
            "",
            "Judge failures by criterion: "
            + ", ".join(f"{k} {v}" for k, v in s["judge_criteria_failures"].items()),
        ]
    if "repeats" in s:
        r = s["repeats"]
        lines += [
            "",
            f"## Repeats (k={r['k']})",
            "",
            f"pass@1 {_pct(r['pass_at_1'])}, pass^k {_pct(r['pass_hat_k'])}, "
            f"outcome consistency {_pct(r['outcome_consistency'])}, "
            f"tool-sequence consistency {_pct(r['tool_sequence_consistency'])}",
        ]
    if "gates" in report:
        g = report["gates"]
        lines += [
            "",
            f"## Quality gates: {'PASSED' if g['passed'] else 'FAILED'}",
            "",
            "| check | actual | threshold | result |",
            "|---|---|---|---|",
        ]
        for c in g["checks"]:
            verdict = "ok" if c["passed"] else "FAIL"
            lines.append(f"| {c['name']} | {c['actual']} | {c['op']} {c['threshold']} | {verdict} |")
        for k in g.get("known_failures", []):
            lines += ["", f"Known failure (quarantined) `{k['case_id']}`: {k['status']}. {k['reason']}"]
    if s["failures"]:
        lines += ["", "## Failures", ""]
        for f in s["failures"]:
            reasons = "; ".join(f"{k}: {v}" for k, v in f["failed"].items())
            judge = f" | judge: {f['judge']}" if f["judge"] else ""
            lines.append(f"- `{f['case_id']}` (r{f['repeat']}, {f['lang']}) {reasons}{judge}")
    return "\n".join(lines) + "\n"


HTML_STYLE = """body{font:14px/1.45 system-ui,sans-serif;margin:2rem auto;max-width:1100px;color:#1f2328}
table{border-collapse:collapse;margin:.5rem 0 1.5rem}
td,th{border:1px solid #d0d7de;padding:4px 10px;text-align:left}
th{background:#f6f8fa}.pass{color:#1a7f37}.fail{color:#cf222e;font-weight:600}details{margin:.3rem 0}
pre{background:#f6f8fa;padding:.6rem;overflow-x:auto;white-space:pre-wrap}summary{cursor:pointer}"""


def to_html(report: dict[str, Any], markdown: str) -> str:
    esc = html.escape
    rows = []
    for r in report["results"]:
        ok = r["passed"] and not (r["judge"] and r["judge"]["verdict"] == "fail")
        status = '<span class="pass">pass</span>' if ok else '<span class="fail">fail</span>'
        failed = "; ".join(f"{k}: {v['details']}" for k, v in r["scores"].items() if not v["passed"])
        if r["judge"] and r["judge"]["verdict"] == "fail":
            failed += f" | judge: {r['judge']['reason']}"
        transcript = "\n\n".join(
            f"[{m['role']}{'/' + m['name'] if m.get('name') else ''}] {m['content']}"
            for m in r["episode"]["messages"]
        )
        rows.append(
            f"<details><summary>{status} <b>{esc(r['case_id'])}</b> (r{r['repeat']}, {esc(r['lang'])}) "
            f"{esc(failed)}</summary><pre>{esc(transcript)}</pre></details>"
        )
    summary = _markdown_to_html(markdown)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(report['run']['id'])}</title>"
        f"<style>{HTML_STYLE}</style></head><body>{summary}<h2>Cases</h2>{''.join(rows)}</body></html>"
    )


def _markdown_to_html(markdown: str) -> str:
    """Tiny converter for the subset of Markdown that to_markdown() emits."""
    out, table = [], []

    def flush() -> None:
        if table:
            head, *body = [
                [html.escape(c.strip()) for c in row.strip("|").split("|")]
                for row in table
                if not set(row) <= set("|-")
            ]
            out.append(
                "<table><tr>"
                + "".join(f"<th>{c}</th>" for c in head)
                + "</tr>"
                + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in body)
                + "</table>"
            )
            table.clear()

    for line in markdown.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("# "):
            out.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            out.append(f"<p>{html.escape(line[2:]).replace('**', '')}</p>")
        elif line.strip():
            out.append(f"<p>{html.escape(line)}</p>")
    flush()
    return "".join(out)


def write_reports(report: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    markdown = to_markdown(report)
    (out_dir / "summary.md").write_text(markdown, encoding="utf-8")
    (out_dir / "report.html").write_text(to_html(report, markdown), encoding="utf-8")
