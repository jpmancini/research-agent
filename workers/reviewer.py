from __future__ import annotations
import json
import re
from fastapi import APIRouter, Depends
from strands import Agent
from strands.models.litellm import LiteLLMModel
from auth import require_role
from contracts import HandoffPacket, ReviewResult, Claim
from tool_registry import GROQ_MODEL, fetch_page
from utils import call_agent_with_backoff, check_goal_drift

router = APIRouter()

_result_cache: dict[str, ReviewResult] = {}

_SYSTEM_PROMPT = (
    "You are a research reviewer. Given a page excerpt, extract key factual claims "
    "relevant to the research question. Return JSON with keys: "
    "'findings' (2-3 sentence summary), "
    "'claims' (list of objects: text, source_url, confidence), "
    "'contradictions' (list of strings), "
    "'confidence' (0.0-1.0 — how certain you are the claims are factually correct), "
    "'quality' (0.0-1.0 — you know what quality is), "
    "'open_questions' (list of strings, max 2)."
)


def _new_agent() -> Agent:
    # Fresh agent per request — Strands accumulates history on reuse
    return Agent(model=LiteLLMModel(model_id=GROQ_MODEL), system_prompt=_SYSTEM_PROMPT)


def _build_prompt(packet: HandoffPacket, url: str, page_text: str) -> str:
    parts = [
        f"Research question: {packet.research_question}",
        f"Source URL: {url}",
        f"Page content:\n{page_text}",
    ]
    if packet.context_summary:
        parts.append(f"Prior findings (check for contradictions):\n{packet.context_summary[:400]}")
    parts.append("Extract claims and return only the JSON object.")
    return "\n\n".join(parts)


@router.post("/review", response_model=ReviewResult, dependencies=[Depends(require_role("reviewer"))])
async def review(packet: HandoffPacket) -> ReviewResult:
    if packet.task_id in _result_cache:
        return _result_cache[packet.task_id]

    url_match = re.search(r'https?://\S+', packet.task)
    url = url_match.group() if url_match else ""

    try:
        # Fetch page directly — no agent tool loop, no context accumulation
        page_text = fetch_page(url) if url else "[No URL provided]"

        response_text = await call_agent_with_backoff(
            _new_agent(), _build_prompt(packet, url, page_text)
        )

        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        try:
            data = json.loads(json_match.group()) if json_match else {}
        except json.JSONDecodeError:
            data = {}

        claims = []
        for c in data.get("claims", []):
            if isinstance(c, dict):
                try:
                    claims.append(Claim(
                        text=c.get("text", ""),
                        source_url=c.get("source_url", url),
                        confidence=float(c.get("confidence", 0.7)),
                    ))
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
                confidence=float(data.get("confidence") or 0.7),
                quality=float(data.get("quality") or 0.7),
                claims=claims,
                contradictions=data.get("contradictions", []),
                tried=[url] if url else [],
                open_questions=data.get("open_questions", []),
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
