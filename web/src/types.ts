// Types mirror PRD §6.1 exactly. Backend (PRD §6.2 ORM) must align field-by-field.

export type NodeType = 'user_input' | 'generate' | 'output' | 'asset' | 'condition';

// Condition 节点的隐式 default 分支保留 key（cases 模式下作为兜底 handle）。
// 用户禁止把这个值用作自定义 case key（后端 nlcompile._valid_new_node 也会拒绝）。
export const CONDITION_DEFAULT_BRANCH_KEY = '__default__';
export type ReasoningEffort = 'low' | 'medium' | 'high' | 'xhigh';

export interface DecisionOption {
  label: string;
  description: string;
  recommended: boolean;
}

export interface DecisionRequestContext {
  title: string;
  summary: string;
}

export interface DecisionGroup {
  id: string;
  label: string;
  type: 'single' | 'multi';
  options: DecisionOption[];
  placeholder?: string;
}

export interface DecisionAnswer {
  group_id: string;
  selected: string[];
}

export interface App {
  id: string;
  name: string;
  description: string;
  cover: string | null;
  created_at: string;
  updated_at: string;
  published_at?: string;
  archived_at?: string | null;
  status: 'draft' | 'published';
  visibility: 'public' | 'private';
  market_access: 'cloneable' | 'run_only';
  can_edit: boolean;
  can_clone: boolean;
  can_run: boolean;
  can_view_source: boolean;
  graph: Graph;
}

export interface Graph {
  tools?: {
    disabled_tool_ids?: string[];
  };
  nodes: WorkflowNode[];
  execution_edges: ExecutionEdge[];
  viewport?: { x: number; y: number; zoom: number };
}

export type WikiSourceStatus = 'pending' | 'ready' | 'unsupported' | 'failed' | 'pending_delete';
export type WikiOperationStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled';

export interface WikiInfo {
  id: string;
  purpose: string;
  schema: string;
  current_revision_id: string | null;
  file_count: number;
  source_count: number;
  total_size: number;
  created_at: string;
  updated_at: string;
}

export interface WikiSource {
  id: string;
  path: string;
  name: string;
  mime: string;
  size: number;
  sha256: string;
  status: WikiSourceStatus;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WikiFile {
  path: string;
  size: number;
  sha256: string;
  mime: string;
  download_url: string;
}

export interface WikiOperation {
  id: string;
  source_id?: string | null;
  kind: string;
  status: WikiOperationStatus;
  instruction?: string | null;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface WikiRevision {
  id: string;
  parent_revision_id?: string | null;
  message: string;
  tree_hash: string;
  file_count: number;
  created_at: string;
  current: boolean;
}

export interface WikiLintResult {
  ok: boolean;
  issues: { severity: 'error' | 'warning' | 'info'; path?: string | null; detail: string }[];
}

export interface WikiAccess {
  app_id: string;
  graph_sha256: string;
  has_wiki: boolean;
  owner_app: boolean;
  requires_consent: boolean;
  granted: boolean;
}

export interface WorkflowLintIssue {
  severity: 'error' | 'warning' | 'info';
  code: string;
  title: string;
  detail: string;
  node_id?: string | null;
  edge_id?: string | null;
  suggestion?: string | null;
}

export interface WorkflowLintResult {
  ok: boolean;
  summary: {
    errors: number;
    warnings: number;
    infos: number;
  };
  issues: WorkflowLintIssue[];
}

interface GraphNodeSize {
  width: number;
  height: number;
}

export type GraphNodeSizeMap = Record<string, GraphNodeSize>;

interface NodeBase {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  title: string;
  description?: string;
}

type OutputContractType = 'json' | 'html' | 'artifact';
export type ArtifactContractKind =
  | 'image'
  | 'code'
  | 'html'
  | 'markdown'
  | 'csv'
  | 'excel'
  | 'docx'
  | 'ppt'
  | 'pdf'
  | 'archive'
  | 'zip'
  | 'file';

export interface NodeOutputContract {
  type: OutputContractType;
  json_schema?: Record<string, unknown>;
  artifact_kind?: ArtifactContractKind;
  max_count?: number;
  validate_office_documents?: boolean;
}

export interface UserInputNode extends NodeBase {
  type: 'user_input';
  input_schema: {
    label: string;
    placeholder?: string;
    kind: 'text' | 'file';
    required?: boolean;
  };
}

export interface GenerateNode extends NodeBase {
  type: 'generate';
  prompt: string;
  model?: string;
  reasoning_effort?: ReasoningEffort;
  output_contract?: NodeOutputContract;
}

export interface OutputNode extends NodeBase {
  type: 'output';
  prompt: string;
  model?: string;
  reasoning_effort?: ReasoningEffort;
}

export interface UploadRef {
  id: string;
  name: string;
  mime: string;
  size: number;
  created_at: string;
}

interface TextAssetNode extends NodeBase {
  type: 'asset';
  asset_kind: 'text';
  content: string;
}

interface UrlAssetNode extends NodeBase {
  type: 'asset';
  asset_kind: 'url';
  urls: string[];
}

interface FileAssetNode extends NodeBase {
  type: 'asset';
  asset_kind: 'file';
  uploads: UploadRef[];
}

interface DrawingAssetNode extends NodeBase {
  type: 'asset';
  asset_kind: 'drawing';
  upload: UploadRef | null;
}

export type AssetNode = TextAssetNode | UrlAssetNode | FileAssetNode | DrawingAssetNode;

export interface ConditionBranch {
  key: string; // 用作 edge.branch_key 与 LLM 输出匹配；binary 模式固定 'true'/'false'
  label?: string;
}

export interface ConditionBranchOverride {
  node_id: string;
  branch_key: string;
}

export interface ConditionResult {
  chosen_branch: string;
  unchosen_branches: string[];
  reason: string;
  raw_answer?: string | null;
  forced: boolean;
}

export interface ConditionNode extends NodeBase {
  type: 'condition';
  mode: 'binary' | 'cases';
  prompt: string;
  model?: string;
  reasoning_effort?: ReasoningEffort;
  branches: ConditionBranch[]; // binary 时固定 [{key:'true'},{key:'false'}]
}

export type WorkflowNode =
  | UserInputNode
  | GenerateNode
  | OutputNode
  | AssetNode
  | ConditionNode;

export interface ExecutionEdge {
  id: string;
  source: string;
  target: string;
  branch_key?: string;
}

export type FailureKind = 'runtime' | 'contract' | 'routing' | 'integrity' | 'internal';

export interface Run {
  id: string;
  app_id: string;
  status: 'pending' | 'running' | 'waiting_for_user' | 'interrupted' | 'success' | 'failed' | 'cancelled';
  name?: string | null;
  inputs: Record<string, unknown>;
  graph: Graph;
  steps: Step[];
  started_at: string;
  finished_at?: string;
  error?: string;
  failure_kind?: FailureKind | null;
  source_run_id?: string | null;
  rerun_from_node_id?: string | null;
  recovery?: RunRecovery | null;
}

export type RunSummary = Pick<
  Run,
  | 'id'
  | 'app_id'
  | 'status'
  | 'name'
  | 'inputs'
  | 'started_at'
  | 'finished_at'
  | 'error'
  | 'failure_kind'
  | 'source_run_id'
  | 'rerun_from_node_id'
>;

export interface Step {
  node_id: string;
  status: 'pending' | 'running' | 'waiting_for_user' | 'interrupted' | 'success' | 'checkpoint_reused' | 'failed' | 'skipped' | 'cancelled';
  input: unknown;
  output: unknown;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  error?: string;
  failure_kind?: FailureKind | null;
  reused_from_run_id?: string | null;
  reused_from_step_id?: string | null;
  logs: LogLine[];
}

interface RunTraceChunk {
  event_id: number;
  type: 'text' | 'tool_call' | 'tool_result' | 'error' | 'done';
  text?: string | null;
  raw?: Record<string, unknown> | null;
}

interface RunTraceArtifact {
  id: string;
  name: string;
  size: number;
  sha256: string;
  integrity: 'verified' | 'modified';
  download_url: string;
  origin_run_id: string;
  origin_artifact_id: string;
  origin_node_id: string;
  origin_node_title: string;
  reused_from_run_id?: string | null;
  reused_from_artifact_id?: string | null;
}

export interface RunArtifact {
  id: string;
  name: string;
  size?: number | null;
  sha256: string;
  integrity: 'verified' | 'modified';
  download_url: string;
  origin_run_id?: string | null;
  origin_artifact_id?: string | null;
  origin_node_id?: string | null;
  origin_node_title?: string | null;
  reused_from_run_id?: string | null;
  reused_from_artifact_id?: string | null;
  mime?: string | null;
}

export interface RunArtifactsOut {
  artifacts: RunArtifact[];
  truncated: boolean;
}

export interface RunStepTrace {
  run_id: string;
  node_id: string;
  node_title: string;
  node_type: 'generate' | 'condition' | 'output';
  status: Step['status'];
  model?: string | null;
  reasoning_effort?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  error?: string | null;
  failure_kind?: FailureKind | null;
  reused_from_run_id?: string | null;
  reused_from_step_id?: string | null;
  prompt: string;
  input?: unknown;
  output?: unknown;
  logs: LogLine[];
  chunks: RunTraceChunk[];
  chunks_truncated: boolean;
  raw_text: string;
  artifacts: RunTraceArtifact[];
  artifacts_truncated: boolean;
}

interface RunRecovery {
  resumable: boolean;
  resume_from_node_id?: string | null;
  reason?: string | null;
  waiting_request?: RunWaitingRequest | null;
}

export interface LogLine {
  ts: string;
  level: 'info' | 'warn' | 'error' | 'tool';
  text: string;
}

export interface AppVersion {
  id: string;
  app_id: string;
  label?: string;
  name: string;
  description: string;
  graph: Graph;
  created_at: string;
  is_published?: boolean;
}

// WS event types (PRD §10.2)
export type AgentChunk = {
  type: 'text' | 'tool_call' | 'tool_result' | 'error' | 'done';
  text?: string;
  raw?: Record<string, unknown>;
};

/** decision_request 请求体；与后端 DecisionRequest / WaitingInputRequest 对齐。 */
export interface RunWaitingRequest {
  context: DecisionRequestContext;
  groups: DecisionGroup[];
  request_id: string;
}

export type RunEvent =
  | { event: 'step.start'; node_id: string; ts: string }
  | { event: 'step.log'; node_id: string; log: LogLine }
  | { event: 'step.delta'; node_id: string; chunk: AgentChunk }
  | { event: 'step.waiting'; node_id: string; request: RunWaitingRequest; question?: string }
  | { event: 'step.end'; node_id: string; step: Step }
  | { event: 'run.waiting_for_user'; node_id: string; question?: string }
  | { event: 'run.resumed'; node_id: string }
  | {
      event: 'run.end';
      status: 'success' | 'failed' | 'cancelled';
      error?: string;
      failure_kind?: FailureKind;
    };

// Persistent Codex workspaces -------------------------------------------------

export type WorkspaceRuntimeStatus = 'stopped' | 'starting' | 'ready' | 'busy' | 'error';
export type WorkspaceWikiSyncStatus = 'pending' | 'syncing' | 'ready' | 'conflict' | 'failed';

export interface Workspace {
  id: string;
  name: string;
  description: string;
  runtime_status: WorkspaceRuntimeStatus;
  runtime_started_at?: string | null;
  runtime_last_error?: string | null;
  wiki_base_revision_id?: string | null;
  wiki_sync_status: WorkspaceWikiSyncStatus;
  wiki_sync_error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceCreateInput {
  name: string;
  description?: string;
  source?: {
    kind: 'empty' | 'git';
    repository_url?: string;
    default_branch?: string;
    access_token?: string;
  };
}

export interface WorkspaceSession {
  id: string;
  workspace_id: string;
  title: string;
  thread_id?: string | null;
  status: 'idle' | 'running' | 'waiting' | 'error';
  last_turn_at?: string | null;
  created_at: string;
  updated_at: string;
  match_context?: string | null;
}

export interface WorkspaceSessionsPage {
  items: WorkspaceSession[];
  has_more: boolean;
  next_offset?: number | null;
}

export interface WorkspaceEvent {
  id: number;
  workspace_id: string;
  session_id: string;
  turn_id?: string | null;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface WorkspaceTurn {
  id: string;
  workspace_id: string;
  session_id: string;
  status: 'pending' | 'running' | 'waiting' | 'interrupted' | 'success' | 'failed' | 'cancelled';
  model?: string | null;
  reasoning_effort?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  created_at: string;
}

export interface WorkspaceWikiSyncResult {
  status: WorkspaceWikiSyncStatus;
  base_revision_id?: string | null;
  error?: string | null;
}

export interface WorkspaceFile {
  path: string;
  name: string;
  kind: 'file' | 'directory';
  size: number;
  mime?: string | null;
  updated_at?: string | null;
}

export interface WorkspaceFilePreview {
  path: string;
  mime: string;
  content?: string | null;
  size: number;
  download_url: string;
}

export interface WorkspaceGitConfig {
  repository_url?: string | null;
  default_branch?: string | null;
  token_configured: boolean;
  allowed_hosts: string[];
}

export interface WorkspaceGitOperationResult {
  status: 'success';
  duration_ms: number;
}

export interface WorkspaceWorkflowProposal {
  id: string;
  workspace_id: string;
  session_id?: string | null;
  kind: 'create' | 'update';
  app_id?: string | null;
  name: string;
  description: string;
  base_graph_sha256?: string | null;
  graph: Graph;
  lint: WorkflowLintResult;
  status: 'pending' | 'applied' | 'stale' | 'rejected';
  created_at: string;
  updated_at: string;
  applied_at?: string | null;
}

export interface WorkspaceWorkflowRun {
  run_id: string;
  app_id: string;
  app_name: string;
  status: string;
  session_id?: string | null;
  turn_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

// JSON Patch ops (subset used by nlcompile, RFC 6902-ish)
export type GraphPatch =
  | { op: 'add_node'; node: WorkflowNode }
  | { op: 'remove_node'; id: string }
  | { op: 'update_node'; id: string; patch: Partial<WorkflowNode> }
  | { op: 'add_edge'; edge: ExecutionEdge }
  | { op: 'remove_edge'; id: string };

export interface NlCompilePlan {
  goal_summary: string;
  assumptions: string[];
  data_flow: string[];
  implementation_steps: string[];
  graph_changes: string[];
  expected_inputs: string[];
  expected_outputs: string[];
  acceptance_criteria: string[];
}

export interface CodexStatus {
  installed: boolean;
  // runnable=null 表示尚未跑过真实 smoke；true/false 由 Codex status probe 返回。
  runnable: boolean | null;
  identity?: string | null;
  method?: string | null;
  error?: string | null;
  checked_at: string;
}

export interface CodexAuthFile {
  path: string;
  content: string;
}

export interface CodexConfigFile {
  path: string;
  content: string;
  settings?: MiraSettings;
  auth: CodexAuthFile;
}

export interface CodexSetupState {
  completed: boolean;
}

export interface InstructionFile {
  path: string;
  content: string;
}

export interface PromptTemplate {
  key: string;
  name: string;
  description: string;
  content: string;
  variables: string[];
  updated_at: string;
}

export interface SkillConfig {
  id: string;
  name: string;
  description: string;
  archive_name: string;
  archive_size: number;
  uploaded_at: string;
  enabled: boolean;
  planning_enabled: boolean;
  dependency_status: 'pending' | 'not_required' | 'ready' | 'failed';
  dependency_error: string;
}

export interface SkillMarkdown {
  path: string;
  content: string;
}

export interface McpHeader {
  name: string;
  value: string;
}

export interface McpServerConfig {
  id: string;
  name: string;
  enabled: boolean;
  planning_enabled: boolean;
  url: string;
  headers: McpHeader[];
  env_var_names: string[];
}

export interface ToolConfig {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  planning_enabled: boolean;
}

export interface MiraSettings {
  supported_models: string[];
  workspace_git_allowed_hosts: string[];
  skills: SkillConfig[];
  mcp_servers: McpServerConfig[];
  tools: ToolConfig[];
}
