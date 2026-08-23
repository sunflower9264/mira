from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.decision import DecisionAnswer, DecisionGroup, DecisionRequestContext

# 状态枚举：与前端 web/src/types.ts Run / Step 完全对齐。
RunStatus = Literal[
    "pending",
    "running",
    "waiting_for_user",
    "interrupted",
    "success",
    "failed",
    "cancelled",
]
StepStatus = Literal[
    "pending",
    "running",
    "waiting_for_user",
    "interrupted",
    "success",
    "checkpoint_reused",
    "failed",
    "skipped",
    "cancelled",
]
FailureKind = Literal["runtime", "contract", "routing", "integrity", "internal"]


class RunAttachmentRef(BaseModel):
    """Run.inputs、NL compile 和 resume payload 中的附件引用。

    后端只透传 id（必须归当前用户）+ 客户端展示用的 name；mime/size/path 等
    元数据由 services/uploads.resolve_upload 在落地到 step 时补齐。
    """

    id: str
    name: str | None = None


class RunInputValue(BaseModel):
    """user_input 节点的输入项；兼容旧的纯字符串形式（在 service 层归一）。"""

    value: str = ""
    attachments: list[RunAttachmentRef] = Field(default_factory=list)


class RunCreateIn(BaseModel):
    """POST /api/runs 请求体。

    inputs 的 key 必须等于 graph 中 user_input 节点的 id；value 允许 str（向后兼容）
    或 RunInputValue 形态，由 service 层 normalize 成 RunInputValue。
    """

    app_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "RunCreateIn":
        if not self.app_id or not isinstance(self.app_id, str):
            raise ValueError("app_id 必填")
        return self


class ConditionBranchOverrideIn(BaseModel):
    """rerun-from 的 condition 分支测试覆盖。"""

    node_id: str
    branch_key: str

    @model_validator(mode="after")
    def _validate(self) -> "ConditionBranchOverrideIn":
        if not self.node_id or not isinstance(self.node_id, str):
            raise ValueError("condition node_id 必填")
        if not self.branch_key or not isinstance(self.branch_key, str):
            raise ValueError("condition branch_key 必填")
        return self


class RunRerunFromIn(BaseModel):
    """POST /api/runs/{id}/rerun-from 请求体。"""

    app_id: str
    node_id: str
    inputs: dict[str, Any] | None = None
    condition_branch_override: ConditionBranchOverrideIn | None = None

    @model_validator(mode="after")
    def _validate(self) -> "RunRerunFromIn":
        if not self.app_id or not isinstance(self.app_id, str):
            raise ValueError("app_id 必填")
        if not self.node_id or not isinstance(self.node_id, str):
            raise ValueError("node_id 必填")
        if self.condition_branch_override is not None and self.condition_branch_override.node_id != self.node_id:
            raise ValueError("condition 分支测试起点必须等于覆盖节点")
        return self


class RunResumeIn(BaseModel):
    """POST /api/runs/{id}/resume 请求体（阶段 4 启用）。"""

    node_id: str
    request_id: str
    answers: list[DecisionAnswer] = Field(default_factory=list)
    text: str | None = None
    attachments: list[RunAttachmentRef] = Field(default_factory=list)


class RunPatchIn(BaseModel):
    """PATCH /api/runs/{id} 请求体。"""

    name: str


class LogLineOut(BaseModel):
    ts: str
    level: Literal["info", "warn", "error", "tool"]
    text: str


class StepOut(BaseModel):
    node_id: str
    status: StepStatus
    input: Any = None
    output: Any = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    failure_kind: FailureKind | None = None
    reused_from_run_id: str | None = None
    reused_from_step_id: str | None = None
    logs: list[LogLineOut] = Field(default_factory=list)


class RunTraceChunkOut(BaseModel):
    event_id: int
    type: Literal["text", "tool_call", "tool_result", "error", "done"]
    text: str | None = None
    raw: dict[str, Any] | None = None


class RunTraceArtifactOut(BaseModel):
    id: str
    name: str
    size: int
    sha256: str
    integrity: Literal["verified", "modified"]
    download_url: str
    origin_run_id: str
    origin_artifact_id: str
    origin_node_id: str
    origin_node_title: str
    reused_from_run_id: str | None = None
    reused_from_artifact_id: str | None = None


class RunArtifactOut(BaseModel):
    id: str
    name: str
    size: int | None = None
    sha256: str
    integrity: Literal["verified", "modified"]
    download_url: str
    origin_run_id: str | None = None
    origin_artifact_id: str | None = None
    origin_node_id: str | None = None
    origin_node_title: str | None = None
    reused_from_run_id: str | None = None
    reused_from_artifact_id: str | None = None
    mime: str | None = None


class RunArtifactsOut(BaseModel):
    artifacts: list[RunArtifactOut] = Field(default_factory=list)
    truncated: bool = False


class RunStepTraceOut(BaseModel):
    run_id: str
    node_id: str
    node_title: str
    node_type: Literal["generate", "condition", "output"]
    status: StepStatus
    model: str | None = None
    reasoning_effort: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    error: str | None = None
    failure_kind: FailureKind | None = None
    reused_from_run_id: str | None = None
    reused_from_step_id: str | None = None
    prompt: str
    input: Any = None
    output: Any = None
    logs: list[LogLineOut] = Field(default_factory=list)
    chunks: list[RunTraceChunkOut] = Field(default_factory=list)
    chunks_truncated: bool = False
    raw_text: str = ""
    artifacts: list[RunTraceArtifactOut] = Field(default_factory=list)
    artifacts_truncated: bool = False


class RunWaitingRequestOut(BaseModel):
    context: DecisionRequestContext
    groups: list[DecisionGroup]
    request_id: str


class RunRecoveryOut(BaseModel):
    resumable: bool = False
    resume_from_node_id: str | None = None
    reason: str | None = None
    waiting_request: RunWaitingRequestOut | None = None


class RunOut(BaseModel):
    id: str
    app_id: str
    status: RunStatus
    name: str | None = None
    inputs: dict[str, Any]
    graph: dict[str, Any]
    steps: list[StepOut] = Field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    failure_kind: FailureKind | None = None
    source_run_id: str | None = None
    rerun_from_node_id: str | None = None
    recovery: RunRecoveryOut | None = None


class RunSummaryOut(BaseModel):
    id: str
    app_id: str
    status: RunStatus
    name: str | None = None
    inputs: dict[str, Any]
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    failure_kind: FailureKind | None = None
    source_run_id: str | None = None
    rerun_from_node_id: str | None = None


class RunCreatedOut(BaseModel):
    run_id: str
    graph: dict[str, Any]
