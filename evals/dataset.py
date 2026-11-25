"""Golden dataset loading and validation.

A case is a conversation (one or more turns) for one signed-in customer plus the
expectations the scorers check. Unknown keys are rejected so a typo in a dataset cannot
silently turn a check off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bank_agent.scenarios import SCENARIOS
from bank_agent.seed import CUSTOMERS

DATASET_DIR = Path(__file__).parent / "datasets"
SUITES = ("tool_use", "safety", "injection", "rag")
LANGUAGES = ("en", "ru", "hy")
TOOLS = (
    "get_accounts",
    "get_balance",
    "list_transactions",
    "list_cards",
    "block_card",
    "transfer_money",
    "search_faq",
)
EXPECT_KEYS = {
    "tools",
    "forbidden",
    "forbidden_patterns",
    "facts",
    "state",
    "faq_docs",
    "canaries",
    "allow",
    "judge_notes",
    "language",
}
ALLOW_VALUES = {"execute_transfer", "block_card"}
CASE_KEYS = {"id", "lang", "customer", "turns", "scenarios", "expect", "tags", "description"}


class DatasetError(ValueError):
    pass


@dataclass
class Case:
    id: str
    suite: str
    lang: str
    customer: str
    turns: list[dict[str, str]]
    expect: dict[str, Any]
    scenarios: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def expected_language(self) -> str:
        return self.expect.get("language", self.lang)


def _tool_names(spec: Any) -> list[str]:
    names = spec["name"] if isinstance(spec, dict) else spec
    return names if isinstance(names, list) else [names]


def validate(case: Case) -> list[str]:
    problems: list[str] = []
    if case.suite not in SUITES:
        problems.append(f"unknown suite {case.suite}")
    if case.lang not in LANGUAGES:
        problems.append(f"unknown lang {case.lang}")
    if case.customer not in {c.id for c in CUSTOMERS}:
        problems.append(f"unknown customer {case.customer}")
    for scenario in case.scenarios:
        if scenario not in SCENARIOS:
            problems.append(f"unknown scenario {scenario}")
    if not case.turns or "user" not in case.turns[-1]:
        problems.append("turns must end with a user turn")
    for turn in case.turns:
        if len(turn) != 1 or not set(turn) <= {"user", "assistant"}:
            problems.append(f"bad turn {turn}")
    unknown = set(case.expect) - EXPECT_KEYS
    if unknown:
        problems.append(f"unknown expect keys {sorted(unknown)}")
    for spec in case.expect.get("tools", []) + case.expect.get("forbidden", []):
        for name in _tool_names(spec):
            if name not in TOOLS:
                problems.append(f"unknown tool {name}")
    bad_allow = set(case.expect.get("allow", [])) - ALLOW_VALUES
    if bad_allow:
        problems.append(f"unknown allow values {sorted(bad_allow)}")
    if case.expected_language not in LANGUAGES:
        problems.append(f"unknown expected language {case.expected_language}")
    return problems


def load_file(path: Path) -> list[Case]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    suite = data["suite"]
    cases = []
    for raw in data["cases"]:
        unknown = set(raw) - CASE_KEYS
        if unknown:
            raise DatasetError(f"{path.name}: case {raw.get('id')} has unknown keys {sorted(unknown)}")
        cases.append(
            Case(
                id=raw["id"],
                suite=suite,
                lang=raw["lang"],
                customer=raw["customer"],
                turns=raw["turns"],
                expect=raw.get("expect") or {},
                scenarios=raw.get("scenarios") or [],
                tags=raw.get("tags") or [],
                description=raw.get("description", ""),
            )
        )
    return cases


def load_cases(
    suites: list[str] | None = None,
    tags: list[str] | None = None,
    ids: list[str] | None = None,
    directory: Path = DATASET_DIR,
) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.yaml")):
        cases.extend(load_file(path))
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise DatasetError(f"duplicate case id {case.id}")
        seen.add(case.id)
        problems = validate(case)
        if problems:
            raise DatasetError(f"{case.id}: {'; '.join(problems)}")
    if suites and suites != ["all"]:
        cases = [c for c in cases if c.suite in suites]
    if tags:
        cases = [c for c in cases if set(tags) & set(c.tags)]
    if ids:
        cases = [c for c in cases if c.id in ids]
    return cases
