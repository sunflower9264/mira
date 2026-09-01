from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.decision import DecisionAnswer
from app.schemas.runs import RunAttachmentRef


WorkspaceRuntimeStatus = Literal["stopped", "starting", "ready", "busy", "error"]
WorkspaceWikiSyncStatus = Literal["pending", "syncing", "ready", "conflict", "failed"]


class WorkspaceSourceIn(BaseModel):
    kind: Literal["empty", "git"] = "empty"
    repository_url: str | None = Field(default=None, max_length=2048)
    default_branch: str | None = Field(default=None, max_length=255)
    access_token: str | None = Field(default=None, max_length=8192)

    @model_validator(mode="after")
    def validate_git(self):
        if self.kind == "git" and not (self.repository_url or "").strip():
            raise ValueError("Git 来源必须填写仓库地址")
        return self


class WorkspaceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    source: WorkspaceSourceIn = Field(default_factory=WorkspaceSourceIn)


class WorkspacePatchIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=4000)


class WorkspaceOut(BaseModel):
    id: str
    name: str
    description: str
    runtime_status: WorkspaceRuntimeStatus
    runtime_started_at: str | None = None
    runtime_last_error: str | None = None
    wiki_base_revision_id: str | None = None
    wiki_sync_status: WorkspaceWikiSyncStatus
    wiki_sync_error: str | None = None
    created_at: str
    updated_at: str


class WorkspaceSessionCreateIn(BaseModel):
    title: str = Field(default="新会话", max_length=120)


class WorkspaceSessionPatchIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class WorkspaceSessionOut(BaseModel):
    id: str
    workspace_id: str
    title: str
    thread_id: str | None = None
    status: Literal["idle", "running", "waiting", "error"]
    last_turn_at: str | None = None
    created_at: str
    updated_at: str


class WorkspaceEventOut(BaseModel):
    id: int
    workspace_id: str
    session_id: str
    turn_id: str | None = None
    event_type: str
    payload: dict[str, Any]
    created_at: str


class WorkspaceTurnCreateIn(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    attachments: list[RunAttachmentRef] = Field(default_factory=list)
    model: str | None = Field(default=None, max_length=120)
    reasoning_effort: str | None = Field(default=None, max_length=24)


class WorkspaceTurnResumeIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=255)
    answers: list[DecisionAnswer] = Field(default_factory=list)
    text: str | None = None
    attachments: list[RunAttachmentRef] = Field(default_factory=list)


class WorkspaceTurnOut(BaseModel):
    id: str
    workspace_id: str
    session_id: str
    status: Literal["pending", "running", "waiting", "interrupted", "success", "failed", "cancelled"]
    model: str | None = None
    reasoning_effort: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    created_at: str


class WorkspaceSessionActionIn(BaseModel):
    action: Literal["compact", "archive", "review"]
    instructions: str | None = Field(default=None, max_length=4000)


class WorkspaceGoalIn(BaseModel):
    objective: str | None = Field(default=None, max_length=8000)
    status: Literal["active", "complete", "blocked"] | None = None
    token_budget: int | None = Field(default=None, ge=1)


class WorkspaceFileOut(BaseModel):
    path: str
    name: str
    kind: Literal["file", "directory"]
    size: int
    mime: str | None = None
    updated_at: str | None = None


class WorkspaceFilesOut(BaseModel):
    files: list[WorkspaceFileOut] = Field(default_factory=list)


class WorkspaceFilePreviewOut(BaseModel):
    path: str
    mime: str
    size: int
    content: str | None = None
    download_url: str


class WorkspaceGitConfigIn(BaseModel):
    repository_url: str = Field(min_length=1, max_length=2048)
    default_branch: str = Field(default="main", min_length=1, max_length=255)
    access_token: str | None = Field(default=None, max_length=8192)


class WorkspaceGitConfigOut(BaseModel):
    repository_url: str | None = None
    default_branch: str | None = None
    token_configured: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)


class WorkspaceWorkflowProposalCreateIn(BaseModel):
    session_id: str | None = None
    kind: Literal["create", "update"]
    app_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8000)
    base_graph_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    graph: dict[str, Any]

    @model_validator(mode="after")
    def validate_target(self):
        if self.kind == "update" and not self.app_id:
            raise ValueError("修改工作流必须指定 app_id")
        if self.kind == "create" and self.app_id:
            raise ValueError("新建工作流不能指定 app_id")
        return self


class WorkspaceWorkflowProposalOut(BaseModel):
    id: str
    workspace_id: str
    session_id: str | None = None
    kind: Literal["create", "update"]
    app_id: str | None = None
    name: str
    description: str
    base_graph_sha256: str | None = None
    graph: dict[str, Any]
    lint: dict[str, Any]
    status: Literal["pending", "applied", "stale", "rejected"]
    created_at: str
    updated_at: str
    applied_at: str | None = None


class WorkspaceWorkflowRunIn(BaseModel):
    app_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    wiki_mode: Literal["auto", "without"] = "auto"
