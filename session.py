"""
SQLite-backed session management for DAGPlan persistence.

The full DAGPlan (all nodes, statuses, retry counts, results) is
serialized to JSON and written to SQLite after every orchestrator batch.
On restart, resume_plan() resets any 'running' nodes back to 'pending'
so in-flight work retries and completed work is not repeated.

Sessions are identified by UUID. The CLI accepts --session-id to resume
a prior run; the orchestrator also seeds new sessions with relevant
findings from past sessions via the L3 memory store in memory.py.
"""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path
from dag import DAGPlan


DB_PATH = Path("research_agent.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            plan_json  TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


def save_plan(plan: DAGPlan) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO sessions (session_id, plan_json) VALUES (?, ?)",
        (plan.session_id, plan.model_dump_json()),
    )
    conn.commit()
    conn.close()


def load_plan(session_id: str) -> DAGPlan | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT plan_json FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return DAGPlan.model_validate_json(row[0])


def resume_plan(plan: DAGPlan) -> DAGPlan:
    """
    On restart: reset any 'running' nodes back to 'pending' so they retry.
    Nodes marked 'done' are untouched — idempotency guarantees make this safe.
    """
    for node in plan.nodes.values():
        if node.status == "running":
            node.status = "pending"
    return plan
