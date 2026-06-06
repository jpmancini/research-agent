# CLAUDE.md

## Project

Multi-agent research system. A research question goes in, a structured brief comes out.
Three workers (Searcher, Reviewer, Writer) run under a DAG orchestrator.
Context stays coherent via Mem0 memory and typed handoff contracts.

## Architecture in one paragraph

`run.py` sends a question to `POST /orchestrate`. The orchestrator builds a DAG
(two parallel search nodes), dispatches them to `POST /search`, then injects one
`POST /review` node per source returned, then dispatches `POST /write`.
All workers talk to the orchestrator via localhost HTTP even though they share a
process — the boundary is real and extractable to separate services.
SQLite persists the DAG; Mem0 persists findings and dead ends between steps.

## Key files

| File | Role |
|---|---|
| `contracts.py` | All Pydantic models. Touch this first when changing data shapes. |
| `dag.py` | PlanNode, DAGPlan, handle_failure, build_initial_plan. The plan is the source of truth. |
| `session.py` | SQLite save/load. Saves after every orchestrator batch — crash-safe. |
| `memory.py` | Mem0 wrapper. add_finding, add_dead_end, get_context_summary. |
| `workers/` | One file per worker. Each is a FastAPI router + Strands agent + result cache. |
| `orchestrator/main.py` | The DAG loop. Reads ready nodes, dispatches in parallel, handles results. |
| `mock_data.py` | Hardcoded search/fetch results. Swap for Tavily/SerpAPI here only. |

## Invariants — do not break these

- **Idempotency**: every worker checks `_result_cache[task_id]` before doing work.
  The orchestrator retries on crash; double-execution must be safe.
- **Goal anchor**: `research_question` is always in every `HandoffPacket`.
  Never omit it. It is the pinned goal that prevents drift.
- **Typed boundaries**: workers return Pydantic models, not raw strings.
  If you add a field, add it to `contracts.py` first.
- **Mem0 after success only**: write findings to Mem0 after `result.success == True`.
  Do not write failed or partial results — they corrupt the context summary.
- **Save after every batch**: `save_plan(plan)` runs after every orchestrator loop
  iteration, not at the end of the run. A restart loses at most one step.

## Coding style (Karpathy principles)

- **Read every line**. Do not accept code you cannot explain. If a block is unclear,
  rewrite it until it is.
- **Simple over clever**. A flat if/elif chain is better than a dispatch table if the
  cases are few. Prefer boring.
- **No abstraction before the third use**. Two similar blocks is fine. Three earns a helper.
- **Small, testable units**. Each function does one thing. If you need to write "and"
  to describe it, split it.
- **Explicit over implicit**. No magic. If a worker needs context, pass it explicitly
  in the HandoffPacket — do not rely on global state.
- **Names over comments**. `classify_failure(result)` needs no comment.
  `process(data, mode)` needs a rewrite, not a docstring.
- **Make it work, then make it right**. Get the end-to-end loop running before
  optimizing anything. The DAG loop is the critical path.

## What not to do

- Do not add new fields to `HandoffPacket` or `HandoffResult` without considering
  all workers that send or receive them.
- Do not write findings to Mem0 inside a worker. Only the orchestrator writes to Mem0
  after receiving a result. Workers are stateless.
- Do not swallow exceptions silently. Workers catch exceptions and return
  `failure_type="worker_crash"` — that is intentional. Do not add bare `except: pass`.
- Do not change the Strands agent system prompts without testing the JSON parsing
  in the worker — the prompt shapes the output format the parser expects.
- Do not add real API calls to `mock_data.py`. That file is the seam. Real search
  belongs in a separate module imported there.

## Running

```bash
source .venv/bin/activate
uvicorn app:app --reload                                      # start server
python run.py "your question here"                            # new run
python run.py "your question" --session-id <id>              # resume
curl http://localhost:8000/health                             # sanity check
```

## Testing a layer in isolation

```python
# contracts
from contracts import HandoffPacket; HandoffPacket(session_id="x", research_question="q", task="t")

# DAG
from dag import build_initial_plan; p = build_initial_plan("s1", "q"); print(p.ready_nodes())

# session
from session import save_plan, load_plan; from dag import build_initial_plan
p = build_initial_plan("s2", "q"); save_plan(p); assert load_plan("s2").session_id == "s2"

# single worker (requires GROQ_API_KEY)
from contracts import HandoffPacket; from workers.searcher import search; import asyncio
asyncio.run(search(HandoffPacket(session_id="s3", research_question="brain sim", task="search")))
```

## Environment

```
GROQ_API_KEY=...        # required — llama-3.3-70b-versatile via LiteLLM
MEM0_API_KEY=...        # optional — defaults to local storage
```
