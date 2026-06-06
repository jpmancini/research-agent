# Development Plan

## Goal
Generic multi-agent research system. Input: any question. Output: structured brief. Three specialized workers coordinate over a dynamic DAG.

## Data Contracts (contracts.py)

**HandoffPacket** — sent from orchestrator to every worker:
- `task_id` — unique identifier for this node in the DAG
- `session_id` — run-scoped identifier (used as Mem0 `user_id`)
- `research_question` — the original question, always present as a goal anchor
- `task` — the specific instruction for this worker invocation
- `context_summary` — populated from a fresh Mem0 retrieval before every dispatch
- `already_tried` — list of approaches that have already failed
- `max_steps` — budget cap passed to the Strands agent loop

**HandoffResult** — returned by every worker:
- `task_id` — echoed from the packet
- `success` — bool
- `failure_type` — one of `worker_crash`, `logic_failure`, `tool_failure`, `plan_failure`
- `failure_reason` — human-readable string
- `findings` — free-text summary of what was learned
- `confidence` — float 0–1
- `tried` — tools or approaches attempted
- `open_questions` — questions the worker could not answer
- `suggested_next` — optional hint for the orchestrator

**SearchResult** extends HandoffResult — adds `sources` (list of ranked sources with title, url, snippet).

**ReviewResult** extends HandoffResult — adds `claims` (list of extracted key claims) and `contradictions` (list of conflicts with prior Mem0 findings).

**WriteResult** extends HandoffResult — adds `brief` (the full structured research brief as a string) and `word_count`.

## The DAG

The plan is dynamic. It is not fixed at creation time — it grows as real results arrive.

**Initial plan:** two parallel search nodes (`search_approaches`, `search_milestones`), no dependencies between them.

**After search completes:** the orchestrator inspects the sources list from each SearchResult and injects one review node per source found. These review nodes are parallel; each depends only on the search node that produced its source.

**After all reviews complete:** a single `write_brief` node is injected, depending on every review node.

```
[search_approaches]  [search_milestones]   <- parallel, no dependencies
         |                   |
[review_source_0] [review_source_1] ...   <- parallel, injected dynamically
         |                   |
              [write_brief]               <- depends on all review nodes
```

## Orchestrator Loop

1. Query DAG for all nodes whose dependencies are complete — these are the ready nodes.
2. For each ready node, retrieve the top 5 relevant memories from Mem0 using the node's task as the query, and build a HandoffPacket with the result as `context_summary`.
3. Dispatch all ready nodes in parallel via `asyncio.gather`.
4. For each result:
   - On success: write findings to Mem0, and if the completed node was a search node, inject one review node per source into the DAG.
   - On failure: call `handle_failure` with the failure type and result.
5. Save the full DAG to SQLite after every batch.
6. Repeat until the DAG has no ready nodes and no in-progress nodes.

## Failure Recovery (four types)

- **worker_crash** — the worker process died or timed out. Retry the same task up to 3 times. After 3 failures, mark the node as blocked and continue.
- **logic_failure** (confidence < 0.4) — the worker completed but was not confident in its output. Retry the same task with the prior attempt's `tried` list appended to `already_tried` in the packet, so the worker knows what it already attempted.
- **tool_failure** — a specific tool (e.g. a search API) failed. Store the failed tool in Mem0 as `"DEAD END: [tool] failed — [reason]"` so no future worker retries it. Then retry the task without that tool.
- **plan_failure** — the worker reports that the task itself is not achievable as specified. Call the LLM with the failed subtree and ask it to replan: generate replacement nodes that approach the goal differently.

## Memory Design

- **Write to Mem0** after every successful worker result, using `findings` as the memory text and `session_id` as `user_id`.
- **Read from Mem0** (top 5 relevant memories) before every worker dispatch to populate `context_summary`. This replaces manual context compression — the retrieval is semantic, not positional.
- **Dead ends** are stored as `"DEAD END: [reason]"` so they surface in future retrievals and suppress known-bad approaches.
- `session_id` is used as `user_id` in all Mem0 calls, scoping memory to the current run.

## Build Sequence

1. contracts.py
2. mock_data.py
3. dag.py
4. session.py
5. memory.py
6. workers/searcher.py
7. workers/reviewer.py
8. workers/writer.py
9. orchestrator/main.py
10. app.py + run.py

## Future Features

- Real web search (Tavily/SerpAPI)
- Cross-session L3 semantic memory via Mem0 `agent_id`
- Prometheus metrics (`context_tokens_used`, `goal_similarity_score`, `retry_rate`)
- Separate worker processes / Docker
- Human-in-the-loop escalation
