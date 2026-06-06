"""
Mem0 wrapper for agent memory across steps and sessions.

L2 (episodic): per-run memory scoped by session_id.
    - Key findings from each worker
    - Dead ends (rate-limited tools, failed searches)
    - Goal anchor reinforcement

Mem0 handles embedding, storage, and semantic retrieval automatically.
This replaces manual context compression — context_summary in every
HandoffPacket is built from a fresh Mem0 search.
"""
from __future__ import annotations
from mem0 import Memory

_mem = Memory()


def add_finding(text: str, session_id: str) -> None:
    """Store a worker finding in session memory."""
    _mem.add(text, user_id=session_id)


def add_dead_end(reason: str, session_id: str) -> None:
    """Store a dead end so future workers don't retry it."""
    _mem.add(f"DEAD END — do not retry: {reason}", user_id=session_id)


def get_context_summary(query: str, session_id: str, limit: int = 5) -> str:
    """
    Retrieve the most relevant prior memories for a query.
    Used to populate HandoffPacket.context_summary before every worker dispatch.
    """
    results = _mem.search(query, user_id=session_id, limit=limit)
    if not results:
        return ""
    # mem0 returns list of dicts with 'memory' key
    memories = [r.get("memory", r.get("text", str(r))) for r in results]
    return "\n".join(f"- {m}" for m in memories)


def clear_session(session_id: str) -> None:
    """Remove all memories for a session. Useful for testing."""
    _mem.delete_all(user_id=session_id)
