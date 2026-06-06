# Design Notes

## Typed Handoff Contracts + Memory-Driven Context

Every runtime boundary uses an explicit Pydantic contract: a `HandoffPacket` goes in, a `HandoffResult` subtype comes out. Nothing passes as a loose dict or free-form string.

The `research_question` field is on every `HandoffPacket` and is always the first thing the worker reads. This is the goal anchor — the LLM cannot drift from the original question regardless of how many steps have elapsed. Goal drift is also detected algorithmically in `utils.py` via keyword overlap: if findings share fewer than 15% of the research question's key terms, the result is flagged as `plan_failure` and replanned.

`context_summary` is not written by hand. Before every dispatch, the orchestrator queries the L2 memory store with the node's task as the search string and injects the top 5 relevant findings. Context compression is semantic and automatic: whatever is most relevant to the current task surfaces, regardless of when it was written.

Dead ends are stored as explicit entries — `"DEAD END: [reason]"`. These surface naturally in future context summaries when a worker is about to attempt something similar.

High-confidence findings (>= 0.7) are also written to the L3 SQLite store and seeded into new sessions on related research questions.

## Dynamic DAG + Four-Type Failure Recovery

The plan is not fixed at creation time. The initial DAG has two nodes: parallel search nodes. After each search completes, the orchestrator injects one review node per source returned. The graph grows at runtime based on actual results. A fixed plan would over-provision review nodes or hard-code an assumption about source count that's wrong for any question that doesn't fit.

Failure recovery has four strategies, each matched to the type that caused it:

- **worker_crash** — blind retry (transient error, the worker may succeed next time)
- **logic_failure** — retry with prior summary appended to task (worker tried something; make it try something different)
- **tool_failure** — write dead end to memory, retry (marks the failure so future workers don't repeat it)
- **plan_failure** — LLM-driven replan via `inject_revised_subtree()` (the approach was wrong; generate a replacement and rewire the DAG)

Mixing these strategies loses information. Retrying a `tool_failure` without tagging the dead end just causes the same failure again. Replanning a `worker_crash` throws away a subtree that probably just hit a transient error.

## Context Bounded Per Request

Strands agents accumulate conversation history across calls on a shared instance. Using `lru_cache` to reuse a singleton agent means that after several requests, the accumulated history alone can exceed the model's context window.

The fix is to create a fresh `Agent` instance per request. Within a single request, context is also pre-bounded: search results are capped at 3 results × 200 char snippets, page fetches are capped at 1500 chars, and `context_summary` injections are capped before the LLM sees them. The model never sees accumulated tool call history — only the pre-processed inputs.

## SQLite for Plan State, Memory Store for Knowledge

These are genuinely different things. The DAG is a structured execution plan — nodes, edges, statuses, retry counts — that belongs in a relational store with explicit schema. Findings, claims, and dead ends are semantic knowledge that needs to be retrieved by meaning, not by key. Mixing them — storing findings as DAG node fields, or storing plan state in the memory store — would make both harder to work with.

The L2 (session) and L3 (global) memory stores use the same interface: `add(text, user_id)` and `search(query, user_id)`. With `MEM0_API_KEY` set, L2 uses Mem0's hosted semantic search. Without it, `_SimpleMemory` provides keyword-scored retrieval. The interface is identical in both cases.

## Runtime Boundaries Enable Extraction

Workers communicate with the orchestrator via HTTP. This boundary is the runtime contract that allows workers to run in-process or out-of-process without changing any worker code. The `WORKER_URLS` dict in the orchestrator is the only difference between monolith and distributed mode. Worker IAM (per-role bearer tokens) means each process can be independently secured and rotated.

## Quality vs. Confidence

Confidence measures whether findings are factually correct. Quality is intentionally undefined — the system prompt says "you know what quality is." This is deliberate: any formal definition immediately becomes a proxy metric the model optimizes against rather than a genuine judgment. The threshold (0.5) enforces a floor; what the model considers below that floor is up to it.

The distinction came from thinking about how to measure the effectiveness of ideas — not just whether something is correct, but whether it is good. Robert Pirsig's *Zen and the Art of Motorcycle Maintenance* and *Lila* argue that Quality precedes any definition you could give it: you recognize it before you can articulate it. Applied here, that means trusting the model's pre-trained judgment rather than specifying what quality means — on the theory that something genuinely good should score higher on average across many evaluations, even if no single evaluation can fully justify the score.

## What I'd Like to Build Next

**Sandboxed runtimes.** Each worker runs as a separate process today, which gives memory isolation but not filesystem or network isolation. The right fix is a `docker-compose.yml` that runs each worker in its own container with a separate filesystem, network namespace, and resource limits (CPU/memory caps). A compromised worker currently has access to the `.env` file and the SQLite DB — containers close that gap.

**Quality metric evaluation.** There is no empirical validation that the quality scores the LLM assigns correlate with result usefulness. The hypothesis is that pre-trained intelligence produces a consistent signal; the honest answer is we don't know yet. The right approach: build a small labeled eval set of research outputs (human-rated high/low quality), run the system, and check whether quality scores separate them. If they don't, the metric isn't earning its keep.

**More tools.** The tool registry currently has `search_web` and `fetch_page`. Useful additions: an academic paper search (Semantic Scholar or arXiv API), a structured data lookup (Wikidata or Wolfram), and a citation-extraction tool that pulls references from a fetched page. Each would be registered the same way — a plain function, JSON-serializable output — and immediately available to any worker.

**Hardened IAM.** Tokens are currently plaintext in `.env` and passed as Bearer headers. Production hardening would include: scoped tokens per worker generated at startup (not static strings), short TTLs with rotation, mutual TLS between orchestrator and workers, and secrets managed outside the filesystem (AWS Secrets Manager, Vault, etc.). The per-role structure is already correct — the credential storage is the weak point.
