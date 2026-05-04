from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


class FreelancerProfile(BaseModel):
    stack: list[str] = Field(default_factory=list)
    cases: list[str] = Field(default_factory=list)
    price_range: str = ""
    raw_text: str = ""


class CaseHit(BaseModel):
    title: str
    stack: str = ""
    price: str = ""
    duration: str = ""
    link: str = ""
    similarity: float = 0.0


class ProjectAnalysis(BaseModel):
    project_type: str = "other"
    key_requirements: list[str] = Field(default_factory=list)
    hidden_requirements: list[str] = Field(default_factory=list)
    tech_stack_hints: list[str] = Field(default_factory=list)
    completeness_score: int = Field(ge=0, le=10, default=5)
    needs_clarification: bool = False
    questions_to_ask: list[str] = Field(default_factory=list)
    complexity: str = "medium"
    red_flags: list[str] = Field(default_factory=list)


class EstimateModule(BaseModel):
    name: str
    hours: float = 0.0
    risk_factor: float = 1.0


class Estimate(BaseModel):
    modules: list[EstimateModule] = Field(default_factory=list)
    total_hours: float = 0.0
    risk_coefficient: float = 1.0
    suggested_price_min: float = 0.0
    suggested_price_max: float = 0.0
    suggested_days: int = 0
    justification: str = ""


class CritiqueResult(BaseModel):
    score: int = Field(ge=0, le=10, default=5)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    should_rewrite: bool = False


class AgentContext(BaseModel):
    title: str
    description: str
    price_hint: Optional[float] = None
    category: Optional[str] = None
    source: str = ""
    url: str = ""
    deadline_hint: Optional[str] = None

    profile: Optional[FreelancerProfile] = None

    competitor_count: Optional[int] = None
    competitor_min_price: Optional[float] = None
    competitor_avg_days: Optional[float] = None

    past_orders: list[CaseHit] = Field(default_factory=list)

    analysis: Optional[ProjectAnalysis] = None
    estimate: Optional[Estimate] = None


class PipelineResult(BaseModel):
    context: AgentContext
    draft_a: str = ""
    draft_b: str = ""
    questions: list[str] = Field(default_factory=list)
    critique: Optional[CritiqueResult] = None
    final_score: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    langfuse_trace_id: Optional[str] = None
