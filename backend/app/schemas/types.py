from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.decision import DecisionGroup, DecisionRequestContext


class AppOut(BaseModel):
    id: str
    name: str
    description: str
    cover: str | None = None
    created_at: str
    updated_at: str
    published_at: str | None = None
    archived_at: str | None = None
    status: Literal["draft", "published"]
    visibility: Literal["public", "private"] = "public"
    market_access: Literal["cloneable", "run_only"] = "cloneable"
    can_edit: bool = True
    can_clone: bool = True
    can_run: bool = True
    can_view_source: bool = True
    graph: dict[str, Any]


class AppVersionOut(BaseModel):
    id: str
    app_id: str
    label: str | None = None
    name: str
    description: str
    graph: dict[str, Any]
    created_at: str
    is_published: bool = False


class CodexConfigContent(BaseModel):
    path: str
    content: str


class CodexConfigFile(BaseModel):
    path: str
    content: str
    auth: CodexConfigContent


class CodexSetupState(BaseModel):
    completed: bool


class CodexConfigSaveIn(BaseModel):
    content: str
    auth_content: str
    supported_models: list[str]


class InstructionFile(BaseModel):
    path: str
    content: str


class InstructionSaveIn(BaseModel):
    content: str


class PromptTemplateOut(BaseModel):
    key: str
    name: str
    description: str = ""
    content: str
    variables: list[str] = Field(default_factory=list)
    updated_at: str


class PromptTemplateSaveIn(BaseModel):
    content: str


class SkillConfig(BaseModel):
    id: str
    name: str
    description: str = ""
    archive_name: str
    archive_size: int
    uploaded_at: str
    enabled: bool = True
    planning_enabled: bool = False
    dependency_status: Literal["pending", "not_required", "ready", "failed"] = "pending"
    dependency_error: str = ""


class SkillMarkdownOut(BaseModel):
    path: str
    content: str


class McpHeader(BaseModel):
    name: str
    value: str


class McpServerConfig(BaseModel):
    id: str
    name: str
    enabled: bool
    planning_enabled: bool = False
    url: str
    headers: list[McpHeader] = Field(default_factory=list)
    env_var_names: list[str] = Field(default_factory=list)


class ToolConfig(BaseModel):
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    planning_enabled: bool = False


class MiraSettings(BaseModel):
    supported_models: list[str] = Field(default_factory=list)
    skills: list[SkillConfig] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    tools: list[ToolConfig] = Field(default_factory=list)
    workspace_git_allowed_hosts: list[str] = Field(default_factory=list)


class WorkspaceGitAllowedHostsIn(BaseModel):
    hosts: list[str] = Field(default_factory=list, max_length=100)


class RuntimeMcpServerConfig(BaseModel):
    name: str
    url: str
    headers: list[McpHeader] = Field(default_factory=list)


class RuntimeSkillConfig(BaseModel):
    id: str
    archive_path: str
    archive_md5: str = ""
    skill_root: str = ""
    dependency_key: str = ""


class SkillUpdateIn(BaseModel):
    enabled: bool | None = None
    planning_enabled: bool | None = None


class UploadOut(BaseModel):
    id: str
    name: str
    mime: str
    size: int
    created_at: str


class NlCompilePlan(BaseModel):
    goal_summary: str
    assumptions: list[str] = Field(default_factory=list)
    data_flow: list[str] = Field(default_factory=list)
    implementation_steps: list[str] = Field(default_factory=list)
    graph_changes: list[str] = Field(default_factory=list)
    expected_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class NlCompilePlannedOut(BaseModel):
    status: Literal["planned"] = "planned"
    compile_id: str
    plan: NlCompilePlan
    plan_markdown: str


class NlCompileCompletedOut(BaseModel):
    status: Literal["completed"] = "completed"
    new_graph: dict[str, Any]
    applied_patches: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    plan_markdown: str


class NlCompileWaitingRequest(BaseModel):
    context: DecisionRequestContext
    groups: list[DecisionGroup]
    request_id: str


class NlCompileWaitingOut(BaseModel):
    status: Literal["waiting_for_user"] = "waiting_for_user"
    compile_id: str
    request: NlCompileWaitingRequest


class NlCompileProgressOut(BaseModel):
    status: Literal["planning", "applying", "interrupted"]
    compile_id: str
    instruction: str | None = None
    request: NlCompileWaitingRequest | None = None
    plan: NlCompilePlan | None = None
    plan_markdown: str | None = None
    error: str | None = None


NlCompileOut = NlCompilePlannedOut | NlCompileCompletedOut | NlCompileWaitingOut | NlCompileProgressOut


class GraphLayoutBeautifyOut(BaseModel):
    graph: dict[str, Any]


class WorkflowLintIssueOut(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str
    title: str
    detail: str
    node_id: str | None = None
    edge_id: str | None = None
    suggestion: str | None = None


class WorkflowLintSummaryOut(BaseModel):
    errors: int = 0
    warnings: int = 0
    infos: int = 0


class WorkflowLintOut(BaseModel):
    ok: bool
    summary: WorkflowLintSummaryOut
    issues: list[WorkflowLintIssueOut] = Field(default_factory=list)


class PromptAssistantOutputContractOut(BaseModel):
    type: Literal["json", "html", "artifact"]
    json_schema: dict | None = None
    artifact_kind: Literal["image", "code", "html", "markdown", "csv", "excel", "docx", "ppt", "pdf", "archive", "zip", "file"] | None = None
    max_count: int | None = None
    validate_office_documents: bool | None = None


class PromptAssistantCompletedOut(BaseModel):
    status: Literal["completed"] = "completed"
    prompt: str
    output_contract: PromptAssistantOutputContractOut | None = None


class PromptAssistantWaitingOut(BaseModel):
    status: Literal["waiting_for_user"] = "waiting_for_user"
    generation_id: str
    request: NlCompileWaitingRequest


class PromptAssistantInterruptedOut(BaseModel):
    status: Literal["interrupted"] = "interrupted"
    generation_id: str
    error: str


PromptAssistantGenerateOut = PromptAssistantCompletedOut | PromptAssistantWaitingOut | PromptAssistantInterruptedOut
