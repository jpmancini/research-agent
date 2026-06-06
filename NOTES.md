# Design Notes

## Contracts
`HandoffPacket` in, `HandoffResult` subtype out at every boundary. `research_question` is on every packet — the goal anchor. Drift is detected in `utils.py` by keyword overlap; < 15% match → `plan_failure`.

## DAG + Failure Recovery
Two parallel search nodes start the plan. Review nodes are injected per source after search completes — the graph grows at runtime. Four failure strategies:
- **worker_crash** — blind retry
- **logic_failure** — retry with prior summary appended
- **tool_failure** — tag dead end in memory, retry
- **plan_failure** — LLM replan via `inject_revised_subtree()`

## Context
Fresh `Agent` per request — Strands accumulates history on reuse. Inputs pre-bounded: 3 search results × 200-char snippets, 1500-char page fetches, 400-char context injections.

## Memory
L2 (session): Mem0 hosted or `_SimpleMemory` keyword fallback — same interface either way. L3 (global): SQLite, keyword-scored cross-session retrieval. DAG state lives in SQLite separately — structured execution plan vs. semantic knowledge are different things.

## Distributed Mode
`WORKER_URLS` in the orchestrator is the only difference between monolith and distributed. Per-role bearer tokens mean each process can be independently secured and rotated.

## Quality vs. Confidence
Confidence = factual correctness. Quality is intentionally undefined ("you know what quality is") — inspired by philosopher Robert Pirsig. Any formal definition becomes a proxy the model games.

## What I'd Like to Build Next
- **Sandboxed runtimes** — Docker containers per worker; currently separate processes but shared filesystem
- **Quality metric evaluation** — labeled eval set to check whether quality scores actually correlate with output usefulness
- **More tools** — academic search (arXiv/Semantic Scholar), structured lookup (Wikidata), citation extraction
- **Hardened IAM** — short-lived tokens, mutual TLS, secrets manager instead of `.env`
