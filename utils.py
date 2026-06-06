from __future__ import annotations
import asyncio
import logging
import re

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "are", "was", "were", "be", "been", "have", "has",
    "what", "how", "why", "when", "where", "which", "who", "that", "this",
    "latest", "recent", "current", "new", "best", "top", "about", "from",
}

DRIFT_THRESHOLD = 0.15  # fraction of question keywords that must appear in findings


def check_goal_drift(findings: str | None, research_question: str) -> bool:
    """
    Returns True if findings appear to have drifted from the research question.
    Uses keyword overlap: if fewer than DRIFT_THRESHOLD fraction of the question's
    meaningful keywords appear in findings, the worker likely went off-topic.
    """
    def keywords(text: str) -> set[str]:
        tokens = re.findall(r"[a-z]+", text.lower())
        return {t for t in tokens if t not in _STOPWORDS and len(t) > 2}

    if not findings:
        return True  # empty findings always count as drift

    q_keys = keywords(research_question)
    if not q_keys:
        return False

    f_keys = keywords(findings)
    overlap = len(q_keys & f_keys) / len(q_keys)
    return overlap < DRIFT_THRESHOLD


async def call_agent_with_backoff(agent, prompt: str, max_retries: int = 4) -> str:
    """
    Run a Strands agent call with exponential backoff on rate limit errors.
    Free Groq tier is 12,000 TPM — parallel calls can exhaust it instantly.
    """
    delay = 10
    for attempt in range(max_retries):
        try:
            return str(agent(prompt))
        except Exception as e:
            err = str(e)
            is_rate_limit = "rate_limit" in err.lower() or "RateLimitError" in err
            if is_rate_limit and attempt < max_retries - 1:
                logger.warning(f"Rate limited — waiting {delay}s before retry {attempt + 1}")
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise
    raise RuntimeError("Max retries exceeded")
