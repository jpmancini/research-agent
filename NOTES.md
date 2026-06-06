# Notes

## What I Went Deep On

### 1. Typed Handoff Contracts + Mem0 Context Management

Every runtime boundary in this system is an explicit Pydantic contract: a HandoffPacket goes in, a HandoffResult (or subtype) comes out. Nothing is passed as a loose dict or free-form string between workers and the orchestrator.

The `research_question` field is present on every HandoffPacket and is always the first thing the worker reads. This is the goal anchor — the LLM cannot drift from the original question because it is always explicitly in scope, no matter how many steps have elapsed or how much context has accumulated.

`context_summary` is not written by hand. Before every dispatch, the orchestrator queries Mem0 with the node's task as the search string and retrieves the top 5 semantically relevant memories. That retrieval result becomes `context_summary`. This means context compression is automatic and semantic rather than a manual summarization step that someone has to maintain. As the run progresses and more findings are written to Mem0, the context summaries get richer without any additional orchestration logic.

Dead ends are stored in Mem0 as explicit entries — `"DEAD END: [tool] failed — [reason]"`. Because Mem0 retrieval is semantic, these surface naturally in future context summaries when a worker is about to attempt something similar. No worker ever retries a known failure unless it explicitly decides to.

### 2. Dynamic DAG + Failure Recovery

The plan is not fixed at creation time. The initial DAG has exactly two nodes: two parallel search nodes. The orchestrator does not assume how many sources those searches will return.

After each search node completes, the orchestrator reads the `sources` list from the SearchResult and injects one review node per source directly into the live DAG. The graph grows at runtime based on actual results. A fixed plan would have to either over-provision review nodes (most of which would be no-ops) or hard-code an assumption about source count that would be wrong for any question that doesn't match the assumption.

Failure recovery has four distinct strategies, each matched to the failure type that caused it:

- **worker_crash** gets a blind retry — the worker may have hit a transient error.
- **logic_failure** (low confidence) gets a retry-with-context — the prior attempt's `tried` list is added to `already_tried` so the worker can try a different approach.
- **tool_failure** gets dead-end tagging plus a retry without the broken tool — the failure is written to Mem0 so it suppresses that tool in future runs within the same session.
- **plan_failure** gets an LLM-driven replan — the orchestrator calls the LLM with the failed subtree and asks it to generate replacement nodes that approach the same goal differently.

Mixing these strategies would lose information. Retrying a tool_failure without tagging the dead end just causes the same failure again. Replanning a worker_crash throws away a subtree that probably just hit a transient error.

## Decisions I'm Most Confident About

- **Mem0 over manual summarization** — the context compression problem is solved better by a purpose-built memory system than by hand-rolled summarization. Manual summarization requires deciding what to keep, which is a judgment call that has to be re-litigated every time the codebase changes. Mem0 retrieval is semantic: whatever is most relevant to the current task surfaces, regardless of when it was written.

- **SQLite for plan state, Mem0 for knowledge** — these are genuinely different things. The DAG is a structured execution plan: nodes, edges, statuses, retry counts. That belongs in a relational store with explicit schema. Findings, claims, and dead ends are semantic knowledge: they need to be retrieved by meaning, not by key. Mixing them — storing findings as DAG node fields, or storing plan state in Mem0 — would make both harder to work with.

- **Dynamic node injection** — the number of sources a search returns is not knowable at plan creation time without running the search. A fixed plan either wastes nodes or misses sources. Injecting review nodes after search completes is the only approach that is correct for any input.

## How I Used Claude Code

I used Claude Code as a coding assistant throughout the build. Specifically:

- **Project scaffolding** — I described the architecture and asked Claude Code to generate the initial file structure and boilerplate. This gave me a working skeleton to iterate on rather than starting from a blank directory.
- **Handoff contract design** — I walked through the HandoffPacket and HandoffResult fields with Claude Code and asked it to flag anything that looked redundant or missing. It caught that I hadn't included `already_tried` in the initial design, which is essential for the logic_failure recovery path.
- **Strands and Mem0 API usage** — I used Claude Code to look up the correct method signatures for Strands agent construction and Mem0 search/add calls, since both libraries have APIs that are easy to get subtly wrong (wrong argument names, wrong return type assumptions).
- **DAG logic validation** — I described the orchestrator loop in plain English and asked Claude Code to check whether the dependency resolution logic would correctly handle the dynamic injection case — specifically, whether injecting new nodes mid-loop would cause any node to be dispatched before its dependencies were complete. It identified one edge case in the ready-node query that I needed to fix.
