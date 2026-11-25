# Recorded results

Every number here comes from real model calls (Claude Haiku 5.5 through the Claude Code CLI, `claude-haiku-5-5`), recorded in `evals/cassettes/` and re-scored offline with `--provider replay`. Latency, tokens and list-price cost are the values recorded at call time. Re-run any of them with the command in the table; replay makes no model calls and gives the same result.

| Folder | What | Reproduce |
|---|---|---|
| `v1/` | baseline prompt, 48 cases | `python -m evals.run --provider replay --prompt v1 --no-judge` (fails the gates on purpose) |
| `v2-draft/` | first fix attempt, stopped after 25 of 48 cases because of regressions F-06 and F-07, with the judge | `python -m evals.run --provider replay --prompt v2 --case <the 25 ids in the report>` |
| `v3/` | current prompt, 48 cases, quality gates | `python -m evals.run --provider replay --prompt v3 --no-judge` |
| `repeats-v1/` | k=3 on the two v1 failures | `python -m evals.run --provider replay --prompt v1 --case tu-ru-block-stolen-credit,rag-hy-safe-account-scam --repeat 3 --no-judge --no-gates` |
| `repeats-v3/` | k=3 on the four unstable cases (tag `k3`) | `python -m evals.run --provider replay --prompt v3 --tag k3 --repeat 3 --no-judge --no-gates` |
| `judge-calibration.*` | judge vs human labels, 21 items, dev and holdout | `python -m evals.calibrate --provider replay` |

Notes:

- v1 includes the 7 harder red-team cases that were added after the first v1 run found no safety or injection failure. They were recorded on v1 before v2 was written, so v1, v2 and v3 are compared on the same cases.
- v1 was re-scored after the scorer fix S-01 (see `docs/findings.md`); before the fix one correct v1 reply was counted as a leak.
- The v3 run had no judge pass, to stay within the call budget. The judge is measured through the calibration set instead.

Real model calls made for this repository: 339. That is 3 CLI isolation checks; v1: 116 agent calls (first run, harder cases, repeats) and 1 judge call; v2 draft: 62 agent and 25 judge calls; v3: 123 agent calls (full run and repeats); calibration: 9 judge calls (the other 12 items reuse judge calls recorded in the v2 run).
