from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


WikiSourceStatus = Literal["pending", "ready", "unsupported", "failed", "pending_delete"]
WikiOperationStatus = Literal["pending", "running", "success", "failed", "cancelled"]


class WikiPatchIn(BaseModel):
    purpose: str | None = None
    schema_: str | None = Field(default=None, alias="schema")


class WikiMaintenanceIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)


class WikiSourcePatchIn(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class WikiAccessGrantIn(BaseModel):
    graph_sha256: str = Field(min_length=64, max_length=64)


class WikiSourceOut(BaseModel):
    id: str
    path: str
    name: str
    mime: str
    size: int
    sha256: str
    status: WikiSourceStatus
    error: str | None = None
    created_at: str
    updated_at: str


class WikiRevisionOut(BaseModel):
    id: str
    parent_revision_id: str | None = None
    message: str
    tree_hash: str
    file_count: int
    created_at: str
    current: bool = False


class WikiOperationOut(BaseModel):
    id: str
    source_id: str | None = None
    kind: str
    status: WikiOperationStatus
    instruction: str | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None


class WikiFileOut(BaseModel):
    path: str
    size: int
    sha256: str
    mime: str
    download_url: str


class WikiOut(BaseModel):
    id: str
    purpose: str
    schema_: str = Field(alias="schema")
    current_revision_id: str | None = None
    file_count: int
    source_count: int
    total_size: int
    created_at: str
    updated_at: str


class WikiFileContentOut(BaseModel):
    path: str
    mime: str
    content: str


class WikiLintIssueOut(BaseModel):
    severity: Literal["error", "warning", "info"]
    path: str | None = None
    detail: str


class WikiLintOut(BaseModel):
    ok: bool
    issues: list[WikiLintIssueOut] = Field(default_factory=list)


class WikiAccessOut(BaseModel):
    app_id: str
    graph_sha256: str
    has_wiki: bool
    owner_app: bool
    requires_consent: bool
    granted: bool
