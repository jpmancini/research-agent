"""
Isolated worker process. Launch one per role:

    WORKER_ROLE=searcher uvicorn workers.app:app --port 8001
    WORKER_ROLE=reviewer uvicorn workers.app:app --port 8002
    WORKER_ROLE=writer   uvicorn workers.app:app --port 8003
"""
import os
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

_ROLE = os.getenv("WORKER_ROLE", "searcher")

_ROUTERS = {
    "searcher": "workers.searcher",
    "reviewer": "workers.reviewer",
    "writer":   "workers.writer",
}

if _ROLE not in _ROUTERS:
    raise ValueError(f"Unknown WORKER_ROLE={_ROLE!r}. Choose: {list(_ROUTERS)}")

import importlib
_mod = importlib.import_module(_ROUTERS[_ROLE])

app = FastAPI(title=f"Worker: {_ROLE}", version="0.1.0")
app.include_router(_mod.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "role": _ROLE}
