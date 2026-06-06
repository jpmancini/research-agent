"""
DAG-based research plan.

A DAGPlan holds PlanNode objects whose execution order is determined by
depends_on edges. The orchestrator calls ready_nodes() each iteration to
find nodes whose dependencies are all done, dispatches them in parallel,
then records results. The plan grows at runtime: inject_review_nodes()
adds one review node per search result, and inject_revised_subtree()
replaces a failed node with a replanned alternative.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal
from contracts import HandoffResult, SearchResult, Source
import uuid


class PlanNode(BaseModel):
    id: str
    node_type: Literal["search", "review", "write"]
    task: str
    worker_endpoint: str            # "/search" | "/review" | "/write"
    depends_on: list[str] = Field(default_factory=list)
    status: Literal["pending", "running", "done", "failed", "blocked"] = "pending"
    retry_count: int = 0
    result: HandoffResult | None = None


class DAGPlan(BaseModel):
    session_id: str
    research_question: str
    nodes: dict[str, PlanNode] = Field(default_factory=dict)

    def ready_nodes(self) -> list[PlanNode]:
        """Nodes that are pending and whose dependencies are all done."""
        return [
            n for n in self.nodes.values()
            if n.status == "pending"
            and all(
                self.nodes[dep].status == "done"
                for dep in n.depends_on
                if dep in self.nodes
            )
        ]

    def is_complete(self) -> bool:
        return all(n.status in ("done", "failed", "blocked") for n in self.nodes.values())

    def has_write_node(self) -> bool:
        return any(n.node_type == "write" for n in self.nodes.values())

    def inject_review_nodes(self, sources: list[Source], depends_on_ids: list[str]) -> None:
        """Dynamically add one review node per source after search completes."""
        review_ids = []
        for i, source in enumerate(sources):
            node_id = f"review_source_{i}_{source.url[-20:].replace('/', '_')}"
            self.nodes[node_id] = PlanNode(
                id=node_id,
                node_type="review",
                task=f"Review and extract claims from: {source.url}\nTitle: {source.title}\nSnippet: {source.snippet}",
                worker_endpoint="/review",
                depends_on=depends_on_ids,
            )
            review_ids.append(node_id)

        # Inject write node depending on all review nodes
        if not self.has_write_node():
            self.nodes["write_brief"] = PlanNode(
                id="write_brief",
                node_type="write",
                task="Write a structured research brief synthesizing all reviewed findings.",
                worker_endpoint="/write",
                depends_on=review_ids,
            )

    def mark_downstream_blocked(self, failed_node_id: str) -> None:
        """Mark all nodes that (transitively) depend on a failed node as blocked."""
        def dependents(node_id: str) -> list[str]:
            return [
                n.id for n in self.nodes.values()
                if node_id in n.depends_on
            ]

        to_block = dependents(failed_node_id)
        visited = set()
        while to_block:
            nid = to_block.pop()
            if nid in visited:
                continue
            visited.add(nid)
            if self.nodes[nid].status not in ("done",):
                self.nodes[nid].status = "blocked"
            to_block.extend(dependents(nid))

    def all_search_node_ids(self) -> list[str]:
        return [n.id for n in self.nodes.values() if n.node_type == "search"]

    def inject_revised_subtree(self, failed_node_id: str, revised_task: str) -> str:
        """
        Replace a failed node with a new node carrying a revised task.
        Nodes that depended on the failed node are rewired to the replacement
        and reset to pending so execution can continue.
        Returns the new node id.
        """
        failed = self.nodes[failed_node_id]
        new_id = f"revised_{failed_node_id}_{str(uuid.uuid4())[:8]}"

        self.nodes[new_id] = PlanNode(
            id=new_id,
            node_type=failed.node_type,
            task=revised_task,
            worker_endpoint=failed.worker_endpoint,
            depends_on=failed.depends_on,
        )

        # Rewire downstream nodes to depend on the replacement
        for node in self.nodes.values():
            if failed_node_id in node.depends_on:
                node.depends_on = [
                    new_id if d == failed_node_id else d
                    for d in node.depends_on
                ]
                if node.status == "blocked":
                    node.status = "pending"

        return new_id


FailureType = Literal["worker_crash", "logic_failure", "tool_failure", "plan_failure"]

CONFIDENCE_THRESHOLD = 0.4
MAX_RETRIES = 3


def classify_failure(result: HandoffResult) -> FailureType:
    if result.failure_type:
        return result.failure_type
    if not result.success and result.confidence < CONFIDENCE_THRESHOLD:
        return "logic_failure"
    return "worker_crash"


def handle_failure(
    node: PlanNode,
    result: HandoffResult,
    plan: DAGPlan,
    mem0_add_fn,           # callable(text: str, session_id: str) -> None
) -> None:
    """
    Four-type failure recovery. mem0_add_fn writes dead ends to memory
    so future workers don't retry known failures.
    """
    failure_type = classify_failure(result)

    if failure_type == "worker_crash":
        if node.retry_count < MAX_RETRIES:
            node.retry_count += 1
            node.status = "pending"
        else:
            node.status = "failed"
            plan.mark_downstream_blocked(node.id)

    elif failure_type == "logic_failure":
        if node.retry_count < MAX_RETRIES:
            node.retry_count += 1
            node.status = "pending"
            # Give next attempt the prior summary so it doesn't start cold
            if result.findings:
                node.task += f"\n\n[Prior attempt summary — do not repeat this approach]: {result.findings[:400]}"
        else:
            node.status = "failed"
            plan.mark_downstream_blocked(node.id)

    elif failure_type == "tool_failure":
        if result.failure_reason:
            mem0_add_fn(
                f"DEAD END — do not retry: {result.failure_reason}",
                plan.session_id,
            )
        node.retry_count += 1
        node.status = "pending"

    elif failure_type == "plan_failure":
        # Mark failed but do NOT block downstream — orchestrator will inject
        # a revised replacement node via inject_revised_subtree.
        node.status = "failed"
        if result.failure_reason:
            mem0_add_fn(
                f"PLAN FAILURE on node {node.id}: {result.failure_reason}",
                plan.session_id,
            )


def build_initial_plan(session_id: str, research_question: str) -> DAGPlan:
    """Create the initial DAG with two parallel search nodes."""
    plan = DAGPlan(session_id=session_id, research_question=research_question)
    plan.nodes["search_approaches"] = PlanNode(
        id="search_approaches",
        node_type="search",
        task=f"Search for current approaches and techniques for: {research_question}",
        worker_endpoint="/search",
        depends_on=[],
    )
    plan.nodes["search_milestones"] = PlanNode(
        id="search_milestones",
        node_type="search",
        task=f"Search for latest milestones, breakthroughs, and recent progress on: {research_question}",
        worker_endpoint="/search",
        depends_on=[],
    )
    return plan
