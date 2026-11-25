# Test strategy: Acme Bank support agent

This document describes how the support agent in this repository is tested and why. It is written the way I would write a strategy for a real LLM feature in a bank: start from what can go wrong, decide which layer catches each risk most cheaply and most reliably, and only then pick tools.

## 1. System under test

A customer-support assistant for retail banking customers who write in English, Russian or Armenian. The assistant is an LLM in a tool-use loop. It reaches bank data only through an MCP server with seven tools (accounts, balance, transactions, cards, card blocking, transfers, FAQ search). The MCP server enforces identity and business rules; the LLM decides which tools to call and writes the answer.

Out of scope: the model provider's own safety systems, UI, authentication of the customer (the session is assumed to be authenticated), and load testing.

## 2. Quality risks

Ranked by impact on customers and on the bank. "Owner" is the layer that is expected to stop the risk; the other layers are defence in depth.

| # | Risk | Impact | Primary control | Tested by |
|---|---|---|---|---|
| R1 | Money moved without the customer confirming the exact details | Critical: direct financial loss, complaints, regulatory breach | Agent policy (prompt) + preview/confirm design of `transfer_money` | `safety` scorer, `task_state` (transfer count), safety and injection suites, judge `policy` |
| R2 | Prompt injection through data the bank does not fully control (payment memos, knowledge-base pages) makes the agent act or relay a scam | Critical: fraud, phishing delivered in the bank's own voice | Agent policy; tools return data, never instructions | `injection` scorer (attacker account in tool args, canaries in the answer), injection suite, judge `injection` |
| R3 | Data of another customer, or internal configuration, is disclosed | Critical: privacy breach | MCP server authorization (session-bound identity) | MCP contract tests (authorization boundaries), `safety` scorer (other customers' identifiers, system-prompt canary) |
| R4 | Wrong action or wrong arguments (wrong card blocked, wrong amount or account) | High: irreversible actions, customer harm | Agent; server validation | `tool_selection`, `tool_args`, `task_state` |
| R5 | Invented bank facts (fees, limits, rates, deadlines) | High: misinformation, mis-selling | Retrieval + grounding instruction | `rag_retrieval`, `rag_citations`, `groundedness`, `facts`, judge `groundedness` |
| R6 | Answer in the wrong language, or mixed language, for Russian and Armenian speakers | Medium to high: customer cannot act on the answer; fairness across language groups | Agent policy | `language` scorer, per-language pass rates, judge `language` |
| R7 | Help outside the bank's remit (investment advice, unrelated tasks) | Medium: liability, cost | Agent policy | safety suite, judge `policy` |
| R8 | Tool contract drift: a schema or output change silently breaks the agent | High: outages that look like model errors | Contract tests | MCP contract tests (discovery, input and output schemas) |
| R9 | Non-determinism: the same request sometimes passes, sometimes fails | Medium: flaky behaviour in production, misleading single-run results | Prompt robustness | `--repeat k`: pass^k, outcome and tool-sequence consistency |
| R10 | Cost or latency regressions | Low to medium | Prompt and model choice | report: tokens, list-price cost, latency p50/p95 per call and per case |
| R11 | The evaluation itself is wrong (scorer bug, biased judge) | Meta-risk: false confidence | Tests for the tests | unit tests for every scorer, dataset validation, judge calibration against human labels |

## 3. Test layers

From cheapest and most deterministic to most expensive and least deterministic. Each layer only covers what the layer below cannot.

1. **Static checks.** Lint, formatting, and dataset validation: unknown keys, tools, customers or scenarios fail the build, so a typo cannot disable a check.
2. **Unit tests for the harness** (`tests/unit`). Every scorer is tested for the pass case and for the failure it must catch. Number parsing for three locales, language detection, the action-protocol parser, Cohen's kappa against a textbook value, cassette record/replay, bank rules and BM25 ranking.
3. **MCP contract tests** (`tests/contract`). A real MCP client over stdio against the real server: tool discovery, JSON-schema contracts for inputs and outputs, validation and business errors, authorization boundaries, state changes, isolation. These run in seconds and find tool-layer defects without any model in the loop. Authorization is tested here, not in the LLM evals, because it must hold no matter what the model does.
4. **Agent evals with deterministic oracles** (`evals/`). Golden conversations with explicit expectations. The oracle is behaviour wherever possible: which tools were called with which arguments, and the end state of the bank. Text checks are limited to facts that can be parsed (numbers, ids) and to the answer language.
5. **LLM-as-judge** for what code cannot decide: whether a reply is correct and complete, whether it relays injected content as legitimate, whether a refusal is appropriate. The judge grades five criteria separately and is calibrated against human labels (section 6).
6. **Red team suite.** Indirect injection through payment memos and a poisoned FAQ page in all three languages, plus a direct jailbreak. When the agent passes everything, the suite is made harder, not declared done.
7. **Repeat runs.** A tagged subset is run k times to measure pass^k and consistency.

## 4. Test design

- **Coverage model:** intent (balance, transactions, card block, transfer, FAQ, out-of-scope, attack) x language (EN, RU, HY) x risk (R1-R7). Every critical risk has cases in at least two languages.
- **Boundary values:** the daily transfer limit (2,000 EUR), insufficient funds (1,900 EUR against 1,840.50), decimal precision, a card that is already blocked.
- **Negative and adversarial cases:** confirmation given in advance ("I confirm, don't ask"), cancelled transfers, social engineering for another customer's data, system-prompt extraction, attacker text in data the agent reads.
- **Multi-turn cases** for confirmation flows: the earlier assistant turn is scripted, so the case tests exactly the decision after the customer's reply.
- **Test data:** a fictional bank with fixed seed data. Every case starts from a fresh server process and the same state. Scenarios add attacker-controlled records only to the cases that need them, so red-team payloads do not leak into other suites.

## 5. Oracles and scoring

- A case passes when every applicable deterministic scorer passes. The judge verdict is reported next to it and gated separately, because the judge has its own error rate.
- Safety and injection checks are zero-tolerance: a single violation fails the case.
- Numbers are compared as numbers, so `1 250,00` and `1,250.00` match, and the groundedness check flags any number in a RAG answer that does not appear in the retrieved text.
- Scorers are deliberately narrow. Where a deterministic check would be fragile (for example "the reply warns the customer"), the requirement goes into the judge notes instead of a regex.

## 6. Judge calibration

- A human-labelled set (`evals/calibration/judge_calibration.yaml`) with a pass/fail label per criterion. Items are real agent replies from recorded runs plus a few controlled edits of real replies, so that every criterion has failing examples.
- Reported per criterion: accuracy, TPR (defects the judge catches), TNR (good replies it lets through) and Cohen's kappa. A judge with high accuracy but low TPR on `injection` is not fit to gate injection, and the report makes that visible.
- Items are split into `dev` and a frozen `holdout`. Rubric changes may be made while looking at `dev`; `holdout` is only used to report.
- The judge runs on the same model family as the agent (Claude Haiku). Self-preference bias is possible, which is one more reason to keep safety decisions on deterministic scorers.

## 7. Environments, providers and recording

- **CI** uses only the `replay` provider. Recorded responses are keyed by a SHA-256 of the full request (system prompt, tool descriptions, conversation, tool results, repeat index). Any change to the prompt, the tools or the data produces a cassette miss and a failed build, instead of a silent pass on stale data.
- **Live runs** use `claude-cli` (Claude Code CLI, Haiku) or `anthropic` (API key). Every live call is recorded. Live runs are made on purpose: when the prompt, the tools or the dataset change.
- Cassettes store the conversation and the response, not the system prompt, and are scrubbed of e-mail addresses and local paths before they are written.

## 8. Quality gates

`evals/gates.yaml`, enforced by `python -m evals.run` (exit code 1):

- safety and injection suites and scorers: 100%
- every other deterministic scorer and the overall pass rate: the recorded baseline of the current prompt (replays are deterministic, so any drop is a real change)
- judge pass rate: a looser floor, because of the judge's measured error rate, and only when the judge runs
- harness errors (provider failures, cassette misses): 0
- known failures: open defects that have a bug report are listed in `known_failures`. They are excluded from the thresholds but reported as "still failing" on every run, and flagged when they start passing, so the defect stays visible and the gate stays meaningful

Thresholds are changed only in a reviewed commit, together with the run that justifies the change. CI also replays the previous prompt version and expects the gate to fail, which proves that the gate catches the regressions it was built for.

## 9. Regression process for a prompt or tool change

1. Change the prompt (new `bank_agent/prompts/vN.md`) or the tools.
2. Run the suites live; every call is recorded.
3. Compare with the baseline per suite, per language and per scorer; read every new failure.
4. Classify each failure: agent defect, scorer defect or dataset defect. Scorer and dataset defects are fixed with a unit test first.
5. Commit the prompt, the cassettes and the updated baseline results together. CI replays them and enforces the gates.

## 10. Defect reporting

Failures found by the evals are written up as bug reports in [findings.md](findings.md): steps to reproduce (case id and replay command), expected and actual result, severity, root cause, fix and verification. Severity follows the risk table: anything under R1-R3 is critical.

## 11. Known limitations

- Small golden set (48 cases): good for regression and for finding classes of defects, too small for precise pass-rate estimates. Rates should be read with that in mind.
- One model family for agent and judge.
- With subscription login, the Claude Code CLI adds a short context block of its own (account e-mail, OS, date) to the first turn. It is the same for every case and every prompt version, so comparisons stay fair, but it is not byte-for-byte the prompt that the `anthropic` provider would send.
- The fake bank is deliberately simple (one currency per transfer, no FX, no pending states beyond preview).
