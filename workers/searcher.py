from __future__ import annotations
import json
from functools import lru_cache
from fastapi import APIRouter
from strands import Agent
from strands.models.litellm import LiteLLMModel
from strands.tools import tool
from contracts import HandoffPacket, SearchResult, Source
from mock_data import mock_search

router = APIRouter()

# Simple in-process result cache for idempotency
_result_cache: dict[str, SearchResult] = {}


@tool
def search_web(query: str) -> str:
    """Search the web for sources relevant to a research query.

    Args:
        query: The search query string. Be specific and include key terms.

    Returns:
        JSON list of sources with url, title, snippet, and relevance_score.
    """
    sources = mock_search(query)
    return json.dumps([s.model_dump() for s in sources])


@lru_cache(maxsize=1)
def _get_agent() -> Agent:
    return Agent(
        model=LiteLLMModel(model_id="groq/llama-3.3-70b-versatile"),
        tools=[search_web],
        system_prompt=(
            "You are a research searcher. Given a research task, use the search_web tool "
            "to find relevant, high-quality sources. Search with specific, targeted queries. "
            "Return your findings as a JSON object with keys: 'findings' (string summary), "
            "'sources' (list of source objects from search results), "
            "'confidence' (0.0-1.0 float), "
            "'open_questions' (list of strings), "
            "'tried' (list of queries you searched)."
        ),
    )


def _build_prompt(packet: HandoffPacket) -> str:
    parts = [
        f"Research question: {packet.research_question}",
        f"Your task: {packet.task}",
    ]
    if packet.context_summary:
        parts.append(f"Prior context:\n{packet.context_summary}")
    if packet.already_tried:
        parts.append(f"Do NOT retry these: {', '.join(packet.already_tried)}")
    parts.append("Search for relevant sources and return structured JSON results.")
    return "\n\n".join(parts)


@router.post("/search", response_model=SearchResult)
async def search(packet: HandoffPacket) -> SearchResult:
    if packet.task_id in _result_cache:
        return _result_cache[packet.task_id]

    try:
        agent = _get_agent()
        response = agent(_build_prompt(packet))
        response_text = str(response)

        # Parse JSON from agent response
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = {}

        sources_raw = data.get("sources", [])
        sources = [Source(**s) if isinstance(s, dict) else s for s in sources_raw]

        # Fallback: if agent didn't return sources, use mock directly
        if not sources:
            sources = mock_search(packet.task)

        result = SearchResult(
            task_id=packet.task_id,
            success=True,
            findings=data.get("findings", f"Found {len(sources)} sources."),
            confidence=float(data.get("confidence", 0.7)),
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
