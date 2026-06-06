from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal
import uuid


def new_task_id() -> str:
    return str(uuid.uuid4())


class Source(BaseModel):
    url: str
    title: str
    snippet: str
    relevance_score: float = Field(ge=0.0, le=1.0)


class Claim(BaseModel):
    text: str
    source_url: str
    confidence: float = Field(ge=0.0, le=1.0)
    contradicts: list[str] = Field(default_factory=list)  # claim texts this conflicts with


class HandoffPacket(BaseModel):
    task_id: str = Field(default_factory=new_task_id)
    session_id: str
    research_question: str          # goal anchor — always present
    task: str                       # what this specific worker should do
    context_summary: str = ""       # relevant Mem0 memories
    already_tried: list[str] = Field(default_factory=list)
    max_steps: int = 10


class HandoffResult(BaseModel):
    task_id: str
    success: bool
    failure_type: Literal["worker_crash", "logic_failure", "tool_failure", "plan_failure"] | None = None
    failure_reason: str | None = None
    findings: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    quality: float = Field(default=0.0, ge=0.0, le=1.0)
    tried: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    suggested_next: str | None = None


class SearchResult(HandoffResult):
    sources: list[Source] = Field(default_factory=list)


class ReviewResult(HandoffResult):
    claims: list[Claim] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class WriteResult(HandoffResult):
    brief: str = ""
    word_count: int = 0
