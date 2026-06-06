"""
Worker IAM: per-role bearer token authentication.

Each worker validates a token specific to its role. The orchestrator
sends the matching token per endpoint. Tokens default to dev values so
the monolith runs without configuration; in production, set them in .env.

Why per-role tokens: if a single token leaks, only one worker is exposed.
The orchestrator rotates out that token without touching the others.
"""
from __future__ import annotations
import os
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

_bearer = HTTPBearer()

# Dev defaults are intentionally obvious — swap in .env for any real deployment.
WORKER_TOKENS: dict[str, str] = {
    "searcher": os.getenv("SEARCHER_TOKEN", "dev-searcher-token"),
    "reviewer": os.getenv("REVIEWER_TOKEN", "dev-reviewer-token"),
    "writer":   os.getenv("WRITER_TOKEN",   "dev-writer-token"),
}


def require_role(role: str):
    """FastAPI dependency: rejects requests that don't carry the correct role token."""
    expected = WORKER_TOKENS[role]

    async def _verify(creds: HTTPAuthorizationCredentials = Security(_bearer)):
        if creds.credentials != expected:
            raise HTTPException(status_code=403, detail=f"Invalid token for role '{role}'")

    return _verify
