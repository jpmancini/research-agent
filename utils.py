from __future__ import annotations
import asyncio
import logging

logger = logging.getLogger(__name__)


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
