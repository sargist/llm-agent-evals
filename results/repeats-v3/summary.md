# Eval run `20261125-053457-claude-cli-v3`

- provider: `claude-cli`, model: `claude-haiku-5-5`, prompt: `v3`, repeat: 3, judge: off
- cases: 4, runs: 12, deterministic pass rate: **100%**, judge pass rate: **n/a**
- LLM calls: 24 agent + 0 judge (16 live in this run), harness errors: 0
- latency per case p50/p95: 8.113s / 10.797s, tokens in/out: 62308/3789, cost at list price: $0.0043

## By suite

| suite | runs | pass rate | judge pass rate |
|---|---|---|---|
| tool_use | 3 | 100% | n/a |
| safety | 3 | 100% | n/a |
| injection | 3 | 100% | n/a |
| rag | 3 | 100% | n/a |

## By language

| language | runs | pass rate | judge pass rate |
|---|---|---|---|
| en | 6 | 100% | n/a |
| hy | 3 | 100% | n/a |
| ru | 3 | 100% | n/a |

## By scorer

| scorer | applicable | passed | pass rate |
|---|---|---|---|
| completed | 12 | 12 | 100% |
| groundedness | 3 | 3 | 100% |
| injection | 3 | 3 | 100% |
| language | 12 | 12 | 100% |
| rag_citations | 3 | 3 | 100% |
| rag_retrieval | 3 | 3 | 100% |
| safety | 12 | 12 | 100% |
| task_state | 6 | 6 | 100% |
| tool_args | 3 | 3 | 100% |
| tool_selection | 6 | 6 | 100% |

## Repeats (k=3)

pass@1 100%, pass^k 100%, outcome consistency 100%, tool-sequence consistency 100%
