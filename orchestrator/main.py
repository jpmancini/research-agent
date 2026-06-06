from __future__ import annotations
import asyncio
import uuid
import json
import logging
import os
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import httpx
import litellm
from contracts import (
    HandoffPacket, HandoffResult, SearchResult, ReviewResult, WriteResult,
    new_task_id,
)
from dag import DAGPlan, PlanNode, handle_failure, build_initial_plan
from session import save_plan, load_plan, resume_plan
from memory import add_finding, add_dead_end, get_context_summary, add_global_finding, get_cross_session_context

logger = logging.getLogger(__name__)
router = APIRouter()

_MONOLITH = "http://localhost:8000"
WORKER_TIMEOUT = 120.0
QUALITY_THRESHOLD = 0.5

# Per-endpoint base URLs. Set env vars to route to separate worker processes;
# fall back to the monolith URL so app.py works unchanged.
WORKER_URLS: dict[str, str] = {
    "/search": os.getenv("SEARCHER_URL", _MONOLITH),
    "/review": os.getenv("REVIEWER_URL", _MONOLITH),
    "/write":  os.getenv("WRITER_URL",   _MONOLITH),
}

# Per-endpoint bearer tokens — must match the WORKER_TOKENS in auth.py.
_WORKER_TOKENS: dict[str, str] = {
    "/search": os.getenv("SEARCHER_TOKEN", "dev-searcher-token"),
    "/review": os.getenv("REVIEWER_TOKEN", "dev-reviewer-token"),
    "/write":  os.getenv("WRITER_TOKEN",   "dev-writer-token"),
}


class RunRequest(BaseModel):
    research_question: str
    session_id: str | None = None


class RunResponse(BaseModel):
    session_id: str
    brief: str
    steps_completed: int
    open_questions: list[str]


async def _call_worker(packet: HandoffPacket, endpoint: str) -> HandoffResult:
    base = WORKER_URLS.get(endpoint, _MONOLITH)
    token = _WORKER_TOKENS.get(endpoint, "")
    async with httpx.AsyncClient(timeout=WORKER_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{base}{endpoint}",
                content=packet.model_dump_json(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
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


def _build_packet(node: PlanNode, plan: DAGPlan, is_new_session: bool = False) -> HandoffPacket:
    context = get_context_summary(node.task, plan.session_id, limit=5)
    # Seed first-round nodes with relevant findings from past sessions
    if is_new_session and node.node_type == "search":
        prior = get_cross_session_context(plan.research_question, limit=3)
        if prior:
            context = prior + ("\n" + context if context else "")
    return HandoffPacket(
        task_id=new_task_id(),
        session_id=plan.session_id,
        research_question=plan.research_question,
        task=node.task,
        context_summary=context,
        already_tried=[],
        max_steps=10,
    )


async def _replan(node: PlanNode, result: HandoffResult, plan: DAGPlan) -> str | None:
    """
    Call the LLM to propose a revised task for a failed node.
    Returns a revised task string, or None if replan itself fails.
    """
    from tool_registry import GROQ_MODEL

    completed = [
        f"- {n.id}: {n.result.findings[:100] if n.result else 'no findings'}"
        for n in plan.nodes.values() if n.status == "done"
    ]

    prompt = (
        f"You are a research orchestrator. A task in your research plan has failed and needs revision.\n\n"
        f"Research question: {plan.research_question}\n\n"
        f"Completed steps so far:\n" + ("\n".join(completed) or "None") + "\n\n"
        f"Failed task: {node.task}\n"
        f"Failure reason: {result.failure_reason or 'unknown'}\n\n"
        f"Propose a single alternative task that achieves the same goal differently. "
        f"Be specific. Return only the revised task description, nothing else."
    )

    try:
        response = await litellm.acompletion(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        revised = response.choices[0].message.content.strip()
        return revised if revised else None
    except Exception as e:
        logger.error(f"Replan LLM call failed: {e}")
        return None


def _event(type: str, **kwargs) -> str:
    return f"data: {json.dumps({'type': type, **kwargs})}\n\n"


async def _run_plan(plan: DAGPlan, queue: asyncio.Queue | None = None, is_new_session: bool = False) -> tuple[str, int, list[str]]:
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

        first_round = steps_completed == 0
        packets = [_build_packet(node, plan, is_new_session=is_new_session and first_round) for node in ready]
        async def _staggered(pkt, node, delay):
            await asyncio.sleep(delay)
            return await _call_worker(pkt, node.worker_endpoint)
        tasks = [_staggered(pkt, node, i * 2) for i, (pkt, node) in enumerate(zip(packets, ready))]
        results = await asyncio.gather(*tasks)

        search_node_ids = plan.all_search_node_ids()
        for node, result in zip(ready, results):
            if result.success:
                # Quality gate: low-quality results trigger replan, same as goal drift
                if result.quality < QUALITY_THRESHOLD and node.node_type != "write":
                    result.success = False
                    result.failure_type = "plan_failure"
                    result.failure_reason = (
                        f"Quality too low ({result.quality:.0%}): {result.findings[:80]}"
                    )

            if result.success:
                node.status = "done"
                node.result = result
                steps_completed += 1

                if result.findings:
                    add_finding(result.findings, plan.session_id)
                    if result.confidence >= 0.7:
                        add_global_finding(result.findings, plan.research_question)
                all_open_questions.extend(result.open_questions)

                await emit("node_done", node_id=node.id, node_type=node.node_type,
                           findings=result.findings[:120] + "..." if len(result.findings) > 120 else result.findings,
                           confidence=result.confidence, quality=result.quality)

                if node.node_type == "search" and isinstance(result, SearchResult):
                    all_done = all(
                        plan.nodes[nid].status in ("done", "failed", "blocked")
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

                if result.failure_type == "plan_failure":
                    revised_task = await _replan(node, result, plan)
                    if revised_task:
                        new_id = plan.inject_revised_subtree(node.id, revised_task)
                        await emit("plan_revised", failed_node=node.id,
                                   new_node=new_id, revised_task=revised_task[:120])
                        logger.info(f"Plan revised: {node.id} -> {new_id}")
                    else:
                        plan.mark_downstream_blocked(node.id)
                        await emit("plan_revision_failed", node_id=node.id)

        save_plan(plan)

    return brief, steps_completed, all_open_questions


def _init_plan(req: RunRequest) -> tuple[DAGPlan, bool]:
    """Returns (plan, is_new_session)."""
    if req.session_id:
        plan = load_plan(req.session_id)
        if plan:
            logger.info(f"Resuming session {req.session_id}")
            return resume_plan(plan), False
    plan = build_initial_plan(str(uuid.uuid4()), req.research_question)
    save_plan(plan)
    return plan, True


@router.post("/orchestrate/stream")
async def orchestrate_stream(req: RunRequest) -> StreamingResponse:
    plan, is_new_session = _init_plan(req)
    queue: asyncio.Queue = asyncio.Queue()

    async def generate():
        # Emit initial plan
        yield _event("plan_created",
                     session_id=plan.session_id,
                     nodes=[{"id": n.id, "type": n.node_type, "depends_on": n.depends_on}
                            for n in plan.nodes.values()])

        async def run():
            brief, steps, questions = await _run_plan(plan, queue, is_new_session=is_new_session)
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
    plan, is_new_session = _init_plan(req)
    brief, steps, open_questions = await _run_plan(plan, is_new_session=is_new_session)
    return RunResponse(
        session_id=plan.session_id,
        brief=brief or "Run incomplete — check logs for failure details.",
        steps_completed=steps,
        open_questions=list(set(open_questions)),
    )
