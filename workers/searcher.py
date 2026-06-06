from __future__ import annotations
import json
import re
from functools import lru_cache
from fastapi import APIRouter
from strands import Agent
from strands.models.litellm import LiteLLMModel
from contracts import HandoffPacket, SearchResult, Source
from mock_data import mock_search
from tool_registry import get_tools
from utils import call_agent_with_backoff

router = APIRouter()

_result_cache: dict[str, SearchResult] = {}


@lru_cache(maxsize=1)
def _get_agent() -> Agent:
    return Agent(
        model=LiteLLMModel(model_id="groq/llama-3.3-70b-versatile"),
        tools=get_tools("searcher"),
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
        response_text = await call_agent_with_backoff(agent, _build_prompt(packet))

        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        data = json.loads(json_match.group()) if json_match else {}

        sources_raw = data.get("sources", [])
        sources = [Source(**s) if isinstance(s, dict) else s for s in sources_raw]

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
