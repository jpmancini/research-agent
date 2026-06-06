from __future__ import annotations
import asyncio
import uuid
import json
import logging
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
from contracts import (
    HandoffPacket, HandoffResult, SearchResult, ReviewResult, WriteResult,
    new_task_id,
)
from dag import DAGPlan, PlanNode, handle_failure, build_initial_plan
from session import save_plan, load_plan, resume_plan
from memory import add_finding, add_dead_end, get_context_summary

logger = logging.getLogger(__name__)
router = APIRouter()

BASE_URL = "http://localhost:8000"
WORKER_TIMEOUT = 120.0


class RunRequest(BaseModel):
    research_question: str
    session_id: str | None = None


class RunResponse(BaseModel):
    session_id: str
    brief: str
    steps_completed: int
    open_questions: list[str]


async def _call_worker(packet: HandoffPacket, endpoint: str) -> HandoffResult:
    async with httpx.AsyncClient(timeout=WORKER_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{BASE_URL}{endpoint}",
                content=packet.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            if endpoint == "/search":
                return SearchResult.model_validate_json(resp.text)
            elif endpoint == "/review":
                return ReviewResult.model_validate_json(resp.text)
            elif endpoint == "/write":
                return WriteResult.model_validate_json(resp.text)
            return HandoffResult.model_validate_json(resp.text)
        except httpx.TimeoutException:
            return HandoffResult(
                task_id=packet.task_id,
                success=False,
                failure_type="worker_crash",
                failure_reason=f"Worker {endpoint} timed out after {WORKER_TIMEOUT}s",
            )
        except Exception as e:
            return HandoffResult(
                task_id=packet.task_id,
                success=False,
                failure_type="worker_crash",
                failure_reason=str(e),
            )


def _build_packet(node: PlanNode, plan: DAGPlan) -> HandoffPacket:
    context = get_context_summary(node.task, plan.session_id, limit=5)
    return HandoffPacket(
        task_id=new_task_id(),
        session_id=plan.session_id,
        research_question=plan.research_question,
        task=node.task,
        context_summary=context,
        already_tried=[],
        max_steps=10,
    )


def _event(type: str, **kwargs) -> str:
    return f"data: {json.dumps({'type': type, **kwargs})}\n\n"


async def _run_plan(plan: DAGPlan, queue: asyncio.Queue | None = None) -> tuple[str, int, list[str]]:
    steps_completed = 0
    brief = ""
    all_open_questions: list[str] = []

    async def emit(type: str, **kwargs):
        if queue:
            await queue.put({"type": type, **kwargs})

    while not plan.is_complete():
        ready = plan.ready_nodes()
        if not ready:
            logger.warning("No ready nodes but plan not complete — possible deadlock")
            await emit("error", message="Deadlock detected — no ready nodes")
            break

        for node in ready:
            node.status = "running"
            await emit("node_dispatched", node_id=node.id, node_type=node.node_type,
                       task=node.task[:80] + "..." if len(node.task) > 80 else node.task)
        save_plan(plan)

        packets = [_build_packet(node, plan) for node in ready]
        async def _staggered(pkt, node, delay):
            await asyncio.sleep(delay)
            return await _call_worker(pkt, node.worker_endpoint)
        tasks = [_staggered(pkt, node, i * 2) for i, (pkt, node) in enumerate(zip(packets, ready))]
        results = await asyncio.gather(*tasks)

        search_node_ids = plan.all_search_node_ids()
        for node, result in zip(ready, results):
            if result.success:
                node.status = "done"
                node.result = result
                steps_completed += 1

                if result.findings:
                    add_finding(result.findings, plan.session_id)
                all_open_questions.extend(result.open_questions)

                await emit("node_done", node_id=node.id, node_type=node.node_type,
                           findings=result.findings[:120] + "..." if len(result.findings) > 120 else result.findings,
                           confidence=result.confidence)

                if node.node_type == "search" and isinstance(result, SearchResult):
                    all_done = all(
                        plan.nodes[nid].status == "done"
                        for nid in search_node_ids if nid in plan.nodes
                    )
                    if all_done and not plan.has_write_node():
                        all_sources = []
                        for nid in search_node_ids:
                            sr = plan.nodes[nid].result
                            if isinstance(sr, SearchResult):
                                all_sources.extend(sr.sources)
                        seen: set[str] = set()
                        unique_sources = [s for s in all_sources if not (s.url in seen or seen.add(s.url))]  # type: ignore
                        plan.inject_review_nodes(unique_sources, search_node_ids)
                        await emit("nodes_injected",
                                   count=len(unique_sources),
                                   urls=[s.url for s in unique_sources])

                if node.node_type == "write" and isinstance(result, WriteResult):
                    brief = result.brief

            else:
                handle_failure(node, result, plan, add_dead_end)
                await emit("node_failed", node_id=node.id,
                           failure_type=result.failure_type,
                           reason=(result.failure_reason or "")[:120])
                logger.warning(f"Node {node.id} failed: {result.failure_reason}")

        save_plan(plan)

    return brief, steps_completed, all_open_questions


def _init_plan(req: RunRequest) -> DAGPlan:
    if req.session_id:
        plan = load_plan(req.session_id)
        if plan:
            logger.info(f"Resuming session {req.session_id}")
            return resume_plan(plan)
    plan = build_initial_plan(str(uuid.uuid4()), req.research_question)
    save_plan(plan)
    return plan


@router.post("/orchestrate/stream")
async def orchestrate_stream(req: RunRequest) -> StreamingResponse:
    plan = _init_plan(req)
    queue: asyncio.Queue = asyncio.Queue()

    async def generate():
        # Emit initial plan
        yield _event("plan_created",
                     session_id=plan.session_id,
                     nodes=[{"id": n.id, "type": n.node_type, "depends_on": n.depends_on}
                            for n in plan.nodes.values()])

        async def run():
            brief, steps, questions = await _run_plan(plan, queue)
            await queue.put({"type": "complete", "brief": brief,
                             "steps_completed": steps, "open_questions": list(set(questions))})

        asyncio.create_task(run())

        while True:
            event = await queue.get()
            yield _event(**event) if "type" in event else _event("unknown")
            if event.get("type") in ("complete", "error"):
                break

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/orchestrate", response_model=RunResponse)
async def orchestrate(req: RunRequest) -> RunResponse:
    plan = _init_plan(req)
    brief, steps, open_questions = await _run_plan(plan)
    return RunResponse(
        session_id=plan.session_id,
        brief=brief or "Run incomplete — check logs for failure details.",
        steps_completed=steps,
        open_questions=list(set(open_questions)),
    )
