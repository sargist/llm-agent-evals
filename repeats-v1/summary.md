# Eval run `20261125-052733-claude-cli-v1`

- provider: `claude-cli`, model: `claude-haiku-5-5`, prompt: `v1`, repeat: 3, judge: off
- cases: 2, runs: 6, deterministic pass rate: **67%**, judge pass rate: **n/a**
- LLM calls: 13 agent + 0 judge (10 live in this run), harness errors: 0
- latency per case p50/p95: 9.065s / 11.382s, tokens in/out: 26964/2617, cost at list price: $0.0029

## By suite

| suite | runs | pass rate | judge pass rate |
|---|---|---|---|
| tool_use | 3 | 67% | n/a |
| rag | 3 | 67% | n/a |

## By language

| language | runs | pass rate | judge pass rate |
|---|---|---|---|
| hy | 3 | 67% | n/a |
| ru | 3 | 67% | n/a |

## By scorer

| scorer | applicable | passed | pass rate |
|---|---|---|---|
| completed | 6 | 6 | 100% |
| groundedness | 3 | 3 | 100% |
| language | 6 | 6 | 100% |
| rag_citations | 3 | 2 | 67% |
| rag_retrieval | 3 | 2 | 67% |
| safety | 6 | 6 | 100% |
| task_state | 3 | 2 | 67% |
| tool_args | 3 | 2 | 67% |
| tool_selection | 6 | 4 | 67% |

## Repeats (k=3)

pass@1 67%, pass^k 0%, outcome consistency 0%, tool-sequence consistency 0%

## Failures

- `rag-hy-safe-account-scam` (r0, hy) tool_selection: not called: search_faq; rag_retrieval: search_faq was not called; rag_citations: faq-009 not cited
- `tu-ru-block-stolen-credit` (r0, ru) tool_selection: not called: block_card; tool_args: no block_card call with {"card_id": "CARD-2002", "reason": "stolen"}; task_state: CARD-2002.status=active, expected blocked
