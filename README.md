# Research Agent

A multi-agent system that takes any research question and produces a structured brief. Three specialized workers — Searcher, Reviewer, and Writer — coordinate through a DAG orchestrator. Context stays coherent across many steps via Mem0 memory and explicit typed handoff contracts.

## Architecture

```
                    ┌──────────────────────────┐
                    │       run.py (CLI)        │
                    │  question → session_id    │
                    └────────────┬─────────────┘
                                 │ HTTP
                    ┌────────────▼─────────────┐
                    │       Orchestrator        │
                    │   DAG loop · recovery     │
                    │   SQLite  ← plan state    │
                    │   Mem0    ← knowledge     │
                    └──┬──────────┬──────────┬──┘
                  HTTP │     HTTP │     HTTP │
             ┌─────────▼┐  ┌─────▼─────┐  ┌▼────────┐
             │ Searcher  │  │ Reviewer  │  │ Writer  │
             │ /search   │  │ /review   │  │ /write  │
             │ Strands   │  │ Strands   │  │ Strands │
             └───────────┘  └───────────┘  └─────────┘
```

## Workers

- **Searcher** — given a query, returns ranked sources with titles and snippets
- **Reviewer** — given a source and the research goal, extracts key claims and flags contradictions with prior sources (uses Mem0 to check prior findings before reviewing)
- **Writer** — given all reviewed claims, writes a structured research brief (no tools — pure LLM synthesis)

## How It Works

1. CLI accepts a research question, creates a session
2. Orchestrator builds an initial DAG with two parallel search nodes
3. After search completes, injects one review node per source found (dynamic plan expansion)
4. After all reviews complete, dispatches Writer
5. Brief written to stdout and saved to session

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add your GROQ_API_KEY to .env
python run.py "What are the latest efforts to simulate a brain in a computer?"
```

## Stack

| Layer | Tool |
|---|---|
| Agent loop | Strands + LiteLLMModel |
| LLM | Groq llama-3.3-70b-versatile (free) |
| Memory | Mem0 (local) |
| Plan state | SQLite |
| API | FastAPI + Pydantic |
| Search/Fetch | Mocked (swap for Tavily/SerpAPI) |

## Deep Dives

Two areas of deliberate depth — see NOTES.md for rationale:

1. **Typed handoff contracts + Mem0 context management** — every worker boundary is an explicit Pydantic contract; Mem0 replaces manual context compression
2. **Dynamic DAG + four-type failure recovery** — plan grows at runtime as search results arrive; each failure type has a distinct recovery strategy

## Future Features

- Real web search (Tavily or SerpAPI — one-line swap in mock_data.py)
- Cross-session semantic memory (Mem0 `agent_id` for persistent L3 knowledge)
- Prometheus metrics: `context_tokens_used`, `goal_similarity_score`, `retry_rate`
- Separate worker processes / Docker containers (routers already decoupled for this)
- Human-in-the-loop escalation on repeated plan failures
