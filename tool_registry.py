from __future__ import annotations
import json
import logging
import os
from strands.tools import tool

logger = logging.getLogger(__name__)

# Override via GROQ_MODEL in .env
# llama-3.1-8b-instant: 500k TPD (5x more than 70b on free tier, fast)
# llama-3.3-70b-versatile: 100k TPD (higher quality, hits limits faster)
GROQ_MODEL = os.getenv("GROQ_MODEL", "groq/llama-3.1-8b-instant")


@tool
def search_web(query: str) -> str:
    """Search the web for sources relevant to a research query.

    Args:
        query: The search query string. Be specific and include key terms.

    Returns:
        JSON list of sources with url, title, snippet, and relevance_score.
    """
    from ddgs import DDGS
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=5):
            results.append({
                "url": r["href"],
                "title": r["title"],
                "snippet": r["body"],
                "relevance_score": 0.8,
            })
    if not results:
        logger.warning(f"search_web returned no results for: {query}")
    return json.dumps(results)


@tool
def fetch_page(url: str) -> str:
    """Fetch and return the text content of a web page.

    Args:
        url: The full URL of the page to fetch.

    Returns:
        The main text content of the page, trimmed to 3000 characters.
    """
    import trafilatura
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        text = trafilatura.extract(downloaded)
        if text:
            return text[:3000]
    logger.warning(f"fetch_page extracted no content from {url}")
    return f"[Could not extract content from {url}]"


REGISTRY: dict[str, dict] = {
    "search_web": {"fn": search_web, "tags": ["search"]},
    "fetch_page":  {"fn": fetch_page,  "tags": ["review"]},
}

WORKER_TOOLS: dict[str, list[str]] = {
    "searcher": ["search"],
    "reviewer": ["review"],
    "writer":   [],
}


def get_tools(worker_type: str) -> list:
    tags = WORKER_TOOLS.get(worker_type, [])
    return [v["fn"] for v in REGISTRY.values()
            if any(t in v["tags"] for t in tags)]
