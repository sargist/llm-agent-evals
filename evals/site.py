"""Build a static site from results/ for GitHub Pages: `python -m evals.site results site`."""

from __future__ import annotations

import html
import shutil
import sys
from pathlib import Path

from evals.report import HTML_STYLE, _markdown_to_html

PAGES = [
    ("v3/report.html", "Prompt v3 (current): 48 cases, quality gates"),
    ("v1/report.html", "Prompt v1 (baseline): 48 cases, fails the v3 gates"),
    ("v2-draft/report.html", "Prompt v2 draft: stopped after 25 cases, with LLM judge"),
    ("repeats-v1/report.html", "k=3 repeats on the v1 failures"),
    ("repeats-v3/report.html", "k=3 repeats on v3"),
    ("judge-calibration.html", "LLM judge calibration: TPR / TNR / kappa per criterion"),
]


def page(title: str, body: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
        f"<style>{HTML_STYLE}</style></head><body>{body}</body></html>"
    )


def build(results: Path, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(results, out)
    calibration = (results / "judge-calibration.md").read_text(encoding="utf-8")
    (out / "judge-calibration.html").write_text(
        page("Judge calibration", _markdown_to_html(calibration)), encoding="utf-8"
    )
    links = "".join(f"<li><a href='{href}'>{html.escape(text)}</a></li>" for href, text in PAGES)
    repo = "https://github.com/sargist/llm-agent-evals"
    body = (
        "<h1>llm-agent-evals: recorded results</h1>"
        "<p>Claude Haiku 5.5 as a banking support agent over an MCP server, evaluated in English, Russian "
        "and Armenian. Every report comes from recorded real model calls and can be reproduced offline "
        "with <code>--provider replay</code>.</p>"
        f"<ul>{links}</ul>"
        f"<p><a href='{repo}'>Source, findings and test strategy on GitHub</a></p>"
    )
    (out / "index.html").write_text(page("llm-agent-evals results", body), encoding="utf-8")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]))
