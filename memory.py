"""
Memory layer for agent findings across steps and sessions.

L2 (episodic): per-run memory scoped by session_id.
    - Key findings from each worker
    - Dead ends (rate-limited tools, failed searches)
    - Goal anchor reinforcement

Two modes:
  - MEM0_API_KEY set: uses Mem0 hosted API with real semantic search
  - No key: lightweight in-process fallback with keyword scoring

The interface is identical in both cases.
"""
from __future__ import annotations
import os


class _SimpleMemory:
    """
    Keyword-scored in-memory fallback. No external deps.
    Swap for Mem0 hosted by setting MEM0_API_KEY in .env.
    """
    def __init__(self) -> None:
        self._store: dict[str, list[str]] = {}

    def add(self, text: str, user_id: str) -> None:
        self._store.setdefault(user_id, []).append(text)

    def search(self, query: str, user_id: str, limit: int = 5) -> list[dict]:
        memories = self._store.get(user_id, [])
        query_words = set(query.lower().split())
        scored = [
            (m, len(query_words & set(m.lower().split())))
            for m in memories
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [{"memory": m} for m, _ in scored[:limit]]

    def delete_all(self, user_id: str) -> None:
        self._store.pop(user_id, None)


def _build_mem():
    api_key = os.getenv("MEM0_API_KEY")
    if api_key:
        from mem0 import MemoryClient
        return MemoryClient(api_key=api_key)
    return _SimpleMemory()


_mem = _build_mem()


def add_finding(text: str, session_id: str) -> None:
    _mem.add(text, user_id=session_id)


def add_dead_end(reason: str, session_id: str) -> None:
    _mem.add(f"DEAD END — do not retry: {reason}", user_id=session_id)


def get_context_summary(query: str, session_id: str, limit: int = 5) -> str:
    results = _mem.search(query, user_id=session_id, limit=limit)
    if not results:
        return ""
    memories = [r.get("memory", r.get("text", str(r))) for r in results]
    return "\n".join(f"- {m}" for m in memories)


def clear_session(session_id: str) -> None:
    _mem.delete_all(user_id=session_id)
