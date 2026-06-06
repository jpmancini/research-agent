from __future__ import annotations
import json
import re
from fastapi import APIRouter, Depends
from strands import Agent
from strands.models.litellm import LiteLLMModel
from auth import require_role
from contracts import HandoffPacket, SearchResult, Source
from tool_registry import GROQ_MODEL, search_web
from utils import call_agent_with_backoff, check_goal_drift

router = APIRouter()

_result_cache: dict[str, SearchResult] = {}

_SYSTEM_PROMPT = (
    "You are a research summarizer. Given a research question and a list of source "
    "snippets, write a concise summary of what the sources say about the question. "
    "Return JSON with keys: "
    "'findings' (string, 2-4 sentences), "
    "'confidence' (0.0-1.0 — how certain you are the findings are factually correct), "
    "'quality' (0.0-1.0 — you know what quality is), "
    "'open_questions' (list of strings, max 3)."
)


def _new_agent() -> Agent:
    # Fresh agent per request — Strands accumulates history on reuse
    return Agent(model=LiteLLMModel(model_id=GROQ_MODEL), system_prompt=_SYSTEM_PROMPT)


def _get_sources(task: str, research_question: str) -> list[Source]:
    """Call search_web directly — no LLM involved, no JSON-in-JSON problem."""
    query = f"{research_question} {task.split(':')[-1].strip()}"[:200]
    try:
        raw = search_web(query)
        results = json.loads(raw)
    except Exception:
        return []

    sources = []
    for s in results:
        if isinstance(s, dict):
            try:
                sources.append(Source(
                    url=s.get("url", ""),
                    title=s.get("title", ""),
                    snippet=s.get("snippet", "")[:200],
                    relevance_score=float(s.get("relevance_score", 0.8)),
                ))
            except Exception:
                pass
    return sources


def _build_summary_prompt(packet: HandoffPacket, sources: list[Source]) -> str:
    snippets = "\n".join(
        f"- [{s.title}]({s.url}): {s.snippet}" for s in sources
    )
    parts = [
        f"Research question: {packet.research_question}",
        f"Sources:\n{snippets}" if snippets else "No sources found.",
    ]
    if packet.context_summary:
        parts.append(f"Prior context:\n{packet.context_summary[:400]}")
    parts.append("Summarize these sources. Return only the JSON object.")
    return "\n\n".join(parts)


@router.post("/search", response_model=SearchResult, dependencies=[Depends(require_role("searcher"))])
async def search(packet: HandoffPacket) -> SearchResult:
    if packet.task_id in _result_cache:
        return _result_cache[packet.task_id]

    try:
        sources = _get_sources(packet.task, packet.research_question)

        response_text = await call_agent_with_backoff(
            _new_agent(), _build_summary_prompt(packet, sources)
        )

        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        try:
            data = json.loads(json_match.group()) if json_match else {}
        except json.JSONDecodeError:
            data = {}

        findings = data.get("findings", f"Found {len(sources)} sources.")

        if check_goal_drift(findings, packet.research_question):
            result = SearchResult(
                task_id=packet.task_id,
                success=False,
                failure_type="plan_failure",
                failure_reason=f"Findings drifted from research question. findings={findings[:120]}",
                sources=sources,
            )
        else:
            result = SearchResult(
                task_id=packet.task_id,
                success=True,
                findings=findings,
                confidence=float(data.get("confidence", 0.7)),
                quality=float(data.get("quality", 0.7)),
                sources=sources,
                tried=data.get("tried", [packet.task]),
                open_questions=data.get("open_questions", []),
            )
    except Exception as e:
        result = SearchResult(
            task_id=packet.task_id,
            success=False,
            failure_type="worker_crash",
            failure_reason=str(e),
            sources=[],
        )

    _result_cache[packet.task_id] = result
    return result
