"""
FastAPI application. Mounts all worker routers and the orchestrator router.
All workers and orchestrator share one process — they communicate via localhost HTTP,
which preserves the runtime boundary contract and makes extraction to separate
services a one-line change per router.
"""
import logging
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

from workers.searcher import router as searcher_router
from workers.reviewer import router as reviewer_router
from workers.writer import router as writer_router
from orchestrator.main import router as orchestrator_router

app = FastAPI(title="Research Agent", version="0.1.0")

app.include_router(searcher_router)
app.include_router(reviewer_router)
app.include_router(writer_router)
app.include_router(orchestrator_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
