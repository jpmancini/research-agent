from __future__ import annotations
import json
import re
from functools import lru_cache
from fastapi import APIRouter
from strands import Agent
from strands.models.litellm import LiteLLMModel
from contracts import HandoffPacket, ReviewResult, Claim
from tool_registry import get_tools, GROQ_MODEL
from utils import call_agent_with_backoff, check_goal_drift

router = APIRouter()

_result_cache: dict[str, ReviewResult] = {}


@lru_cache(maxsize=1)
def _get_agent() -> Agent:
    return Agent(
        model=LiteLLMModel(model_id=GROQ_MODEL),
        tools=get_tools("reviewer"),
        system_prompt=(
            "You are a research reviewer. Given a source URL and research context, "
            "fetch the page content and extract key factual claims. "
            "Compare claims against any prior findings provided in context — flag contradictions. "
            "Return a JSON object with keys: "
            "'findings' (string summary of what this source says), "
            "'claims' (list of objects with text, source_url, confidence 0-1, contradicts list), "
            "'contradictions' (list of contradiction description strings), "
            "'confidence' (0.0-1.0 overall confidence in this source), "
            "'open_questions' (list of strings), "
            "'tried' (list of URLs fetched)."
        ),
    )


def _build_prompt(packet: HandoffPacket) -> str:
    url_match = re.search(r'https?://\S+', packet.task)
    url = url_match.group() if url_match else ""

    parts = [
        f"Research question: {packet.research_question}",
        f"Your task: {packet.task}",
    ]
    if packet.context_summary:
        parts.append(f"Prior findings from other sources:\n{packet.context_summary[:500]}")
    if packet.already_tried:
        parts.append(f"Already reviewed: {', '.join(packet.already_tried)}")
    if url:
        parts.append(f"Fetch and review this URL: {url}")
    parts.append("Extract claims, check for contradictions with prior findings, return structured JSON.")
    return "\n\n".join(parts)


@router.post("/review", response_model=ReviewResult)
async def review(packet: HandoffPacket) -> ReviewResult:
    if packet.task_id in _result_cache:
        return _result_cache[packet.task_id]

    try:
        agent = _get_agent()
        response_text = await call_agent_with_backoff(agent, _build_prompt(packet))

        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        data = json.loads(json_match.group()) if json_match else {}

        claims = []
        for c in data.get("claims", []):
            if isinstance(c, dict):
                try:
                    claims.append(Claim(**c))
                except Exception:
                    pass

        findings = data.get("findings", "Source reviewed.")
        if check_goal_drift(findings, packet.research_question):
            result = ReviewResult(
                task_id=packet.task_id,
                success=False,
                failure_type="plan_failure",
                failure_reason=f"Review findings drifted from research question. findings={findings[:120]}",
                claims=[],
            )
        else:
            result = ReviewResult(
                task_id=packet.task_id,
                success=True,
                findings=findings,
                confidence=float(data.get("confidence", 0.7)),
                claims=claims,
                contradictions=data.get("contradictions", []),
                tried=data.get("tried", []),
                open_questions=data.get("open_questions", []),
                suggested_next=data.get("suggested_next"),
            )
    except Exception as e:
        result = ReviewResult(
            task_id=packet.task_id,
            success=False,
            failure_type="worker_crash",
            failure_reason=str(e),
            claims=[],
        )

    _result_cache[packet.task_id] = result
    return result
