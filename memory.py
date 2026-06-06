"""
Memory layer for agent findings across steps and sessions.

L2 (episodic): per-run memory scoped by session_id.
    - Key findings from each worker
    - Dead ends (rate-limited tools, failed searches)

L3 (cross-session): SQLite-backed global store.
    - High-confidence findings from any session
    - Seeded into new sessions on related research questions via keyword scoring

Two L2 modes:
  - MEM0_API_KEY set: uses Mem0 hosted API with real semantic search
  - No key: lightweight in-process fallback with keyword scoring

The interface is identical in both L2 modes.
"""
from __future__ import annotations
import os
import sqlite3


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


class _Mem0Adapter:
    """
    Wraps the hosted Mem0 MemoryClient to match the _SimpleMemory interface.

    The Mem0 v1 SDK returns {"results": [...]} from search (not a bare list)
    and uses top_k rather than limit. This adapter normalises both so the
    rest of the codebase is unaware of which backend is active.
    """
    def __init__(self, client) -> None:
        self._client = client

    def add(self, text: str, user_id: str) -> None:
        self._client.add(text, user_id=user_id)

    def search(self, query: str, user_id: str, limit: int = 5) -> list[dict]:
        raw = self._client.search(query, user_id=user_id, top_k=limit)
        # SDK returns {"results": [...]} in v1.1
        items = raw.get("results", raw) if isinstance(raw, dict) else raw
        return items if isinstance(items, list) else []

    def delete_all(self, user_id: str) -> None:
        self._client.delete_all(user_id=user_id)


def _build_mem():
    api_key = os.getenv("MEM0_API_KEY")
    if api_key:
        from mem0 import MemoryClient
        return _Mem0Adapter(MemoryClient(api_key=api_key))
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


# ---------------------------------------------------------------------------
# L3: cross-session SQLite store
# ---------------------------------------------------------------------------

def _l3_conn() -> sqlite3.Connection:
    from session import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS global_findings (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            research_topic   TEXT NOT NULL,
            finding          TEXT NOT NULL,
            created_at       TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def add_global_finding(text: str, research_question: str) -> None:
    """Persist a high-confidence finding to the cross-session store."""
    conn = _l3_conn()
    conn.execute(
        "INSERT INTO global_findings (research_topic, finding) VALUES (?, ?)",
        (research_question[:200], text[:1000]),
    )
    conn.commit()
    conn.close()


def get_cross_session_context(research_question: str, limit: int = 3) -> str:
    """
    Return the top-scoring prior findings relevant to research_question.
    Scans the most recent 200 global findings and ranks by keyword overlap.
    """
    conn = _l3_conn()
    rows = conn.execute(
        "SELECT research_topic, finding FROM global_findings ORDER BY created_at DESC LIMIT 200"
    ).fetchall()
    conn.close()

    if not rows:
        return ""

    query_words = set(research_question.lower().split())
    scored = []
    for (topic, finding) in rows:
        # Score against both the stored topic and the finding text
        combined = set((topic + " " + finding).lower().split())
        overlap = len(query_words & combined)
        if overlap > 0:
            scored.append((finding, overlap))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit]
    if not top:
        return ""

    return "\n".join(f"[prior session] {f}" for f, _ in top)
