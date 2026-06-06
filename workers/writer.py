from __future__ import annotations
from functools import lru_cache
from fastapi import APIRouter
from strands import Agent
from strands.models.litellm import LiteLLMModel
from contracts import HandoffPacket, WriteResult

router = APIRouter()

_result_cache: dict[str, WriteResult] = {}


@lru_cache(maxsize=1)
def _get_agent() -> Agent:
    return Agent(
        model=LiteLLMModel(model_id="groq/llama-3.3-70b-versatile"),
        system_prompt=(
            "You are a research writer. Given a collection of reviewed findings and claims, "
            "write a clear, structured research brief for an informed non-specialist audience. "
            "Structure: (1) Overview, (2) Current Approaches, (3) Recent Milestones, "
            "(4) Key Challenges, (5) Expert Disagreements, (6) Conclusion. "
            "Be precise. Cite sources inline. Acknowledge uncertainty where it exists. "
            "Target length: 500-800 words."
        ),
    )


def _build_prompt(packet: HandoffPacket) -> str:
    parts = [
        f"Research question: {packet.research_question}",
        f"Your task: {packet.task}",
    ]
    if packet.context_summary:
        parts.append(f"All reviewed findings:\n{packet.context_summary}")
    parts.append(
        "Write a structured research brief. Return plain text — the full brief, nothing else."
    )
    return "\n\n".join(parts)


@router.post("/write", response_model=WriteResult)
async def write(packet: HandoffPacket) -> WriteResult:
    if packet.task_id in _result_cache:
        return _result_cache[packet.task_id]

    try:
        agent = _get_agent()
        response = agent(_build_prompt(packet))
        brief = str(response).strip()
        word_count = len(brief.split())

        result = WriteResult(
            task_id=packet.task_id,
            success=True,
            findings=brief[:300] + "..." if len(brief) > 300 else brief,
            brief=brief,
            word_count=word_count,
            confidence=0.85,
        )
    except Exception as e:
        result = WriteResult(
            task_id=packet.task_id,
            success=False,
            failure_type="worker_crash",
            failure_reason=str(e),
            brief="",
            word_count=0,
        )

    _result_cache[packet.task_id] = result
    return result
