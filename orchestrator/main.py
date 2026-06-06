from __future__ import annotations
import asyncio
import uuid
import logging
from fastapi import APIRouter
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
WORKER_TIMEOUT = 120.0  # seconds


class RunRequest(BaseModel):
    research_question: str
    session_id: str | None = None   # provide to resume a prior run


class RunResponse(BaseModel):
    session_id: str
    brief: str
    steps_completed: int
    open_questions: list[str]


async def _call_worker(packet: HandoffPacket, endpoint: str) -> HandoffResult:
    """Call a worker endpoint over HTTP. Returns a failure result on timeout or error."""
    async with httpx.AsyncClient(timeout=WORKER_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{BASE_URL}{endpoint}",
                content=packet.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            # Deserialize to the right subtype based on endpoint
            if endpoint == "/search":
                return SearchResult.model_validate_json(resp.text)
            elif endpoint == "/review":
                return ReviewResult.model_validate_json(resp.text)
            elif endpoint == "/write":
                return WriteResult.model_validate_json(resp.text)
            else:
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
    """Build a HandoffPacket for a node, injecting Mem0 context."""
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


async def _run_plan(plan: DAGPlan) -> tuple[str, int, list[str]]:
    """
    Execute the DAG plan to completion.
    Returns (brief, steps_completed, open_questions).
    """
    steps_completed = 0
    brief = ""
    all_open_questions: list[str] = []

    while not plan.is_complete():
        ready = plan.ready_nodes()
        if not ready:
            logger.warning("No ready nodes but plan not complete — possible deadlock")
            break

        # Mark running
        for node in ready:
            node.status = "running"
        save_plan(plan)

        # Dispatch all ready nodes in parallel
        packets = [_build_packet(node, plan) for node in ready]
        tasks = [_call_worker(pkt, node.worker_endpoint) for pkt, node in zip(packets, ready)]
        results = await asyncio.gather(*tasks)

        # Process results
        search_node_ids = plan.all_search_node_ids()
        for node, result in zip(ready, results):
            if result.success:
                node.status = "done"
                node.result = result
                steps_completed += 1

                # Write findings to Mem0
                if result.findings:
                    add_finding(result.findings, plan.session_id)

                # Collect open questions
                all_open_questions.extend(result.open_questions)

                # Dynamic injection: after search nodes complete, inject review nodes
                if node.node_type == "search" and isinstance(result, SearchResult):
                    all_done = all(
                        plan.nodes[nid].status == "done"
                        for nid in search_node_ids
                        if nid in plan.nodes
                    )
                    if all_done and not plan.has_write_node():
                        # Collect all sources from all completed search nodes
                        all_sources = []
                        for nid in search_node_ids:
                            sr = plan.nodes[nid].result
                            if isinstance(sr, SearchResult):
                                all_sources.extend(sr.sources)
                        # Deduplicate by URL
                        seen = set()
                        unique_sources = []
                        for s in all_sources:
                            if s.url not in seen:
                                seen.add(s.url)
                                unique_sources.append(s)
                        plan.inject_review_nodes(unique_sources, search_node_ids)
                        logger.info(f"Injected {len(unique_sources)} review nodes")

                # Capture the brief from the write node
                if node.node_type == "write" and isinstance(result, WriteResult):
                    brief = result.brief

            else:
                handle_failure(node, result, plan, add_dead_end)
                if result.failure_reason:
                    logger.warning(f"Node {node.id} failed: {result.failure_reason}")

        save_plan(plan)

    return brief, steps_completed, all_open_questions


@router.post("/orchestrate", response_model=RunResponse)
async def orchestrate(req: RunRequest) -> RunResponse:
    # Resume or create session
    if req.session_id:
        plan = load_plan(req.session_id)
        if plan:
            plan = resume_plan(plan)
            logger.info(f"Resuming session {req.session_id}")
        else:
            plan = build_initial_plan(req.session_id, req.research_question)
    else:
        session_id = str(uuid.uuid4())
        plan = build_initial_plan(session_id, req.research_question)

    save_plan(plan)
    brief, steps, open_questions = await _run_plan(plan)

    return RunResponse(
        session_id=plan.session_id,
        brief=brief or "Run incomplete — check logs for failure details.",
        steps_completed=steps,
        open_questions=list(set(open_questions)),
    )
