# Research Agent

A multi-agent system that takes any research question and returns a structured brief. Three specialized workers — Searcher, Reviewer, Writer — coordinate through a DAG orchestrator over HTTP. Context stays coherent across many steps via typed handoff contracts and a two-layer memory system.

## Architecture

```
                    run.py (CLI)
                         |
                         | SSE stream
                    Orchestrator  <-- SQLite (plan state + L3 memory)
                    (DAG loop)    <-- Mem0/_SimpleMemory (L2 session memory)
                    /     |     \
                  HTTP   HTTP   HTTP
                  /       |       \
           Searcher   Reviewer   Writer
           /search    /review    /write
           Strands    Strands    Strands
```

Each boundary is a typed `HandoffPacket` in, `HandoffResult` out. The orchestrator never passes loose dicts — all worker communication goes through Pydantic-validated contracts.

## How It Works

1. CLI sends a research question to `/orchestrate/stream`
2. Orchestrator builds an initial DAG: two parallel search nodes
3. Both search nodes run concurrently; results stream back via SSE
4. After search completes, one review node is injected per source found (dynamic plan expansion)
5. All review nodes run in parallel; goal drift and quality are checked on each result
6. A single write node synthesizes all reviewed findings into a structured brief
7. Brief is streamed to stdout and saved to `output/`

Any node failure triggers four-type recovery: transient errors retry, low-confidence results retry with prior context, tool failures are written to memory as dead ends, and plan failures trigger an LLM-driven replan that injects a replacement node into the live DAG.

## Key Concepts

### Tool Registry (`tool_registry.py`)
Tools are registered with tags that determine which workers can use them. `get_tools("searcher")` returns only tools tagged `"search"`. Workers never import tools directly — they always go through the registry. This is the boundary between capability and role.

```python
REGISTRY = {
    "search_web": {"fn": search_web, "tags": ["search"]},
    "fetch_page":  {"fn": fetch_page,  "tags": ["review"]},
}
WORKER_TOOLS = {"searcher": ["search"], "reviewer": ["review"], "writer": []}
```

### Typed Handoffs (`contracts.py`)
Every agent boundary uses an explicit Pydantic contract. `HandoffPacket` carries the `research_question` on every call — the goal anchor that prevents drift regardless of how many steps have elapsed. `HandoffResult` and its subtypes (`SearchResult`, `ReviewResult`, `WriteResult`) enforce what each worker must return.

```python
class HandoffPacket(BaseModel):
    research_question: str   # goal anchor — always present
    task: str                # what this specific worker should do
    context_summary: str     # relevant memories from prior steps
    already_tried: list[str] # dead ends to avoid
```

### DAG Orchestration (`dag.py`)
The plan is a live graph of `PlanNode` objects. It starts with two nodes and grows at runtime: after search completes, the orchestrator injects one review node per source returned. A write node is injected once all reviews are done. Dependencies are tracked explicitly — no node runs until all its `depends_on` nodes are done.

The graph also supports `inject_revised_subtree()`: on a plan failure, a replacement node is added and all downstream dependencies are rewired to point at it.

### Context Management (`utils.py`, `memory.py`)
Two problems solved:

1. **Within-request:** Strands agents accumulate conversation history on reuse. Each worker creates a fresh `Agent` instance per request to keep context bounded. Page fetches and search results are pre-processed before hitting the LLM — no tool-call accumulation.

2. **Across steps:** Before each dispatch, the orchestrator queries the L2 memory store (scoped to the current session) and injects the top 5 relevant findings as `context_summary`. Workers never see the full history — only what's semantically relevant to their current task.

High-confidence findings (>= 0.7) are also written to L3 (SQLite global store) and seeded into new sessions on related topics.

### Session Management (`session.py`)
The full `DAGPlan` (all nodes, statuses, retry counts, results) is serialized to SQLite after every batch. On restart, `resume_plan()` resets any `running` nodes back to `pending` — in-flight work retries, completed work is skipped. Sessions are identified by UUID and can be resumed by passing `--session-id`.

### Runtimes
Workers communicate with the orchestrator via HTTP. This boundary is the runtime contract: a worker can be in-process (monolith), a separate process (distributed), or a remote service — the orchestrator doesn't know or care. The `WORKER_URLS` dict in the orchestrator is the only thing that changes between modes.

### Strands Agents
Each worker wraps a `strands.Agent` with a `LiteLLMModel` backed by Groq. Workers with tools (Searcher: `search_web`, Reviewer: `fetch_page`) have tools registered at construction time. The Writer has no tools — it's pure LLM synthesis. Exponential backoff handles Groq's free-tier rate limits.

### Worker IAM (`auth.py`)
Each worker endpoint requires a per-role bearer token via a FastAPI `Depends` guard. The orchestrator sends the matching token per endpoint. Per-role isolation means a leaked token only exposes one worker. Dev defaults (`dev-*-token`) work without configuration; production tokens are set in `.env`.

### Quality Scoring
Workers return a `quality` score alongside `confidence`. Confidence measures factual certainty. Quality is intentionally undefined — the system prompt says "you know what quality is." Results below `QUALITY_THRESHOLD = 0.5` are failed as `plan_failure` and trigger replan, same as goal drift.

## Running

### Monolith (development)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add GROQ_API_KEY
uvicorn app:app --port 8000

python run.py "What are the latest breakthroughs in quantum error correction?"
python run.py "Your question" --session-id <id>   # resume a prior run
```

### Distributed (separate processes)
```bash
./start_distributed.sh
python run.py "Your question"
```

Each worker runs on its own port (8001–8003). The orchestrator routes to them via `SEARCHER_URL`, `REVIEWER_URL`, `WRITER_URL` env vars. If those are unset, it falls back to the monolith.

## Stack

| Layer | Tool |
|---|---|
| Agent loop | Strands + LiteLLMModel |
| LLM | Groq llama-3.1-8b-instant (default) |
| L2 memory | Mem0 (cloud) or `_SimpleMemory` (local fallback) |
| L3 memory | SQLite global findings store |
| Plan state | SQLite |
| API | FastAPI + Pydantic |
| Search | DuckDuckGo (ddgs) |
| Page fetch | trafilatura |

## Project Structure

```
app.py                  monolith entry point (all routers in one process)
run.py                  CLI client (SSE streaming)
auth.py                 worker IAM (per-role bearer tokens)
contracts.py            Pydantic models: HandoffPacket, HandoffResult subtypes
dag.py                  DAGPlan, PlanNode, failure classification + recovery
memory.py               L2 session memory + L3 cross-session SQLite store
session.py              SQLite plan persistence and resume logic
tool_registry.py        tool definitions, tags, and worker-to-tool mapping
utils.py                goal drift detection, exponential backoff

workers/
  searcher.py           POST /search — DuckDuckGo + LLM summarization
  reviewer.py           POST /review — page fetch + claim extraction
  writer.py             POST /write  — pure LLM synthesis
  app.py                isolated worker process (reads WORKER_ROLE env var)

orchestrator/
  main.py               DAG execution loop, replan, SSE streaming
  app.py                orchestrator-only process entry point

start_distributed.sh    launches all four processes
```
