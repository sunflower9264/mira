from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class AgentProviderStatus(BaseModel):
    installed: bool
    # runnable=None 表示尚未跑过真实 smoke；True/False 由 refresh_agent_status 填。
    runnable: bool | None = None
    identity: str | None = None
    method: str | None = None
    error: str | None = None
    checked_at: str


class AgentProviderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Literal["claude-code", "codex"]
    name: str
    description: str
    runtime: Literal["claude", "codex"]
    enabled: bool
    supported_models: list[str] = Field(default_factory=list)
    status: AgentProviderStatus | None = None


class AgentConfigFile(BaseModel):
    # codex-auth 是虚拟 id：实际对应 DB 中的 auth.json 正文，仅作为 GET 目标读取，
    # 写入合并到 codex 的 PUT（见 AgentConfigSaveIn.auth_content）。
    agent_id: Literal["claude-code", "codex", "codex-auth"]
    path: str
    content: str
    # codex 保存时附带 auth.json 的最新解密元数据，方便前端刷新展示。
    auth: "AgentConfigFile | None" = None


class AgentSetupState(BaseModel):
    completed: bool


class AgentConfigSaveIn(BaseModel):
    content: str
    enabled: bool | None = None
    supported_models: list[str] | None = None
    # 仅 codex 使用：与 content 同步加密保存为 codex-auth 正文。
    auth_content: str | None = None


class InstructionFile(BaseModel):
    provider: Literal["claude-code", "codex"]
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
    provider_ids: list[Literal["claude-code", "codex"]]
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
    agents: list[AgentProviderConfig]
    skills: list[SkillConfig] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    tools: list[ToolConfig] = Field(default_factory=list)


class RuntimeMcpServerConfig(BaseModel):
    name: str
    url: str
    headers: list[McpHeader] = Field(default_factory=list)


class RuntimeSkillConfig(BaseModel):
    id: str
    archive_path: str
    archive_md5: str = ""


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
    tool_use_id: str


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
    artifact_kind: Literal["image", "code", "html", "markdown", "csv", "excel", "docx", "ppt", "pdf", "archive", "file"] | None = None
    max_count: int | None = None


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
