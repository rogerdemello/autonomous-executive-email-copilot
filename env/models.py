from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ActionType = Literal["classify", "reply", "defer", "escalate", "prioritize"]
LabelType = Literal["spam", "normal", "urgent"]
RiskType = Literal["low", "medium", "high"]
SenderRole = Literal["client", "internal", "vendor", "unknown"]
RiskTag = Literal["none", "legal", "security", "finance", "ops"]
PersonaType = Literal["strict_ceo", "balanced", "chill_manager"]
PolicyMode = Literal["baseline", "stress", "llm"]
AIStatusType = Literal["success", "fallback_timeout", "fallback_parse_error", "fallback_validation_error", "provider_error"]


class ThreadEntry(BaseModel):
    from_address: str
    text: str


class EmailRecord(BaseModel):
    id: str
    sender: str
    sender_role: SenderRole
    subject: str
    body: str
    priority_hint: Literal["low", "medium", "high"]
    deadline_minutes: int
    business_value: float = Field(ge=0.0, le=1.0)
    risk_tag: RiskTag = "none"
    thread_history: list[ThreadEntry] = Field(default_factory=list)

    expected_label: LabelType = "normal"
    expected_action: Literal["reply", "defer", "escalate", "ignore"] = "reply"
    expected_reply_keywords: list[str] = Field(default_factory=list)
    recommended_escalation: str | None = None
    critical: bool = False

    predicted_label: LabelType | None = None
    handled_action: Literal["reply", "defer", "escalate"] | None = None
    last_reply: str | None = None
    resolved: bool = False


class ObservationEmail(BaseModel):
    id: str
    sender: str
    sender_role: SenderRole
    subject: str
    body: str
    priority_hint: Literal["low", "medium", "high"]
    deadline_minutes: int
    business_value: float
    risk_tag: RiskTag
    thread_history: list[ThreadEntry]


class Observation(BaseModel):
    emails: list[ObservationEmail]
    time_remaining: int
    pending_actions: list[str]
    risk_level: RiskType
    current_minute: int
    persona: PersonaType
    remaining_interruptions: int


class Action(BaseModel):
    action_type: ActionType
    email_id: str | None = None
    label: LabelType | None = None
    content: str | None = None
    priority_order: list[str] = Field(default_factory=list)
    escalate_to: str | None = None


class ActionResult(BaseModel):
    observation: Observation
    reward: float
    done: bool
    info: dict[str, Any] = Field(default_factory=dict)


class TaskDefinition(BaseModel):
    id: str
    name: str
    difficulty: Literal["easy", "medium", "hard"]
    description: str


class TasksResponse(BaseModel):
    tasks: list[TaskDefinition]
    action_schema: dict[str, Any]
    observation_schema: dict[str, Any]


class AIDecisionTrace(BaseModel):
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    alternatives_considered: list[str] = Field(default_factory=list)
    why_not: str = ""
    latency_ms: int
    model_name: str
    status: AIStatusType
    token_count: int | None = None


class AIResponse(BaseModel):
    action: Action
    trace: AIDecisionTrace


class GraderRequest(BaseModel):
    task_id: str = "hard_full_management"
    seed: int = 42
    persona: PersonaType = "balanced"
    actions: list[Action] = Field(default_factory=list)


class GraderResponse(BaseModel):
    task_id: str
    seed: int
    persona: PersonaType
    score: float
    breakdown: dict[str, float]
    total_reward: float


class BaselineRequest(BaseModel):
    task_id: str = "hard_full_management"
    seed: int = 42
    persona: PersonaType = "balanced"
    mode: PolicyMode = "baseline"
    stress_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    max_steps: int = 100


class BaselineResponse(BaseModel):
    task_id: str
    seed: int
    persona: PersonaType
    mode: PolicyMode
    stress_rate: float
    score: float
    total_reward: float
    steps: int
    breakdown: dict[str, float]
    action_trace: list[Action]
    decision_trace: list[dict[str, Any]] = Field(default_factory=list)


class LeaderboardRequest(BaseModel):
    tasks: list[str] = Field(
        default_factory=lambda: ["easy_classification", "medium_prioritization", "hard_full_management"]
    )
    personas: list[PersonaType] = Field(default_factory=lambda: ["strict_ceo", "balanced", "chill_manager"])
    seeds: list[int] = Field(default_factory=lambda: [42, 43, 44])
    max_steps: int = 120
    mode: PolicyMode = "baseline"
    stress_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    csv_out: str | None = None


class LeaderboardResponse(BaseModel):
    seeds: list[int]
    max_steps: int
    mode: PolicyMode
    stress_rate: float
    csv_out: str | None = None
    rows: list[dict[str, Any]]


class InterruptionEvent(BaseModel):
    trigger_minute: int
    email: EmailRecord


class Scenario(BaseModel):
    task_id: str
    seed: int
    persona: PersonaType
    time_budget: int
    risk_level: RiskType
    emails: list[EmailRecord]
    gold_priority_order: list[str]
    interruptions: list[InterruptionEvent] = Field(default_factory=list)


class StateSnapshot(BaseModel):
    task_id: str
    seed: int
    persona: PersonaType
    time_remaining: int
    current_minute: int
    risk_level: RiskType
    emails: list[EmailRecord]
    action_history: list[Action]
    total_reward: float
    remaining_interruptions: int
