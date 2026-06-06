"""
Orchestrator-only process. Workers run separately.

    uvicorn orchestrator.app:app --port 8000
"""
import logging
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
for noisy in ("LiteLLM", "LiteLLM Proxy", "LiteLLM Router", "httpx", "utils"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

from orchestrator.main import router as orchestrator_router

app = FastAPI(title="Research Agent — Orchestrator", version="0.1.0")
app.include_router(orchestrator_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "role": "orchestrator"}
