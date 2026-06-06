from __future__ import annotations
from strands.tools import tool
from mock_data import mock_search, mock_fetch
import json


@tool
def search_web(query: str) -> str:
    """Search the web for sources relevant to a research query.

    Args:
        query: The search query string. Be specific and include key terms.

    Returns:
        JSON list of sources with url, title, snippet, and relevance_score.
    """
    from contracts import Source
    sources = mock_search(query)
    return json.dumps([s.model_dump() for s in sources])


@tool
def fetch_page(url: str) -> str:
    """Fetch and return the text content of a web page.

    Args:
        url: The full URL of the page to fetch.

    Returns:
        The main text content of the page.
    """
    return mock_fetch(url)


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
