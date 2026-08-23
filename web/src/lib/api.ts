// 前端 API 客户端：所有 HTTP 调用经此文件统一发出。
// 协议约定：
// - 基址使用相对路径 `/api/...`，由 Vite 代理到后端（默认 http://localhost:8000）。
// - 所有需要鉴权的接口自动从 lib/auth 读取 Bearer Token 注入 Authorization header。
// - 后端错误约定：HTTP 非 2xx 时返回 `{ detail: string }`（FastAPI 风格）或 `{ error: string }`，
//   request<T> 会优先取 detail / error 字段，回退到 statusText。
// - 401 全局处理：除登录端点外，任何 401 响应都会清除本地 token+user 摘要，
//   并跳转到 /login（避免在 /login 页面上死循环）。

import type {
  CodexConfigFile,
  CodexSetupState,
  CodexStatus,
  App,
  AppVersion,
  ConditionBranchOverride,
  DecisionAnswer,
  Graph,
  GraphNodeSizeMap,
  GraphPatch,
  InstructionFile,
  McpServerConfig,
  MiraSettings,
  NodeOutputContract,
  NlCompilePlan,
  PromptTemplate,
  ReasoningEffort,
  Run,
  RunArtifactsOut,
  RunSummary,
  RunWaitingRequest,
  RunStepTrace,
  SkillConfig,
  SkillMarkdown,
  UploadRef,
  WorkflowLintResult,
} from '../types';
import { clearToken, clearUser, getToken } from './auth';

// --- HTTP helper ----------------------------------------------------------

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | boolean | number | undefined>;
  signal?: AbortSignal;
}

class ApiError extends Error {
  status: number;
  payload: unknown;
  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined) continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function parseError(response: Response): Promise<{ message: string; payload: unknown }> {
  const text = await response.text();
  if (!text) return { message: response.statusText || `HTTP ${response.status}`, payload: null };
  try {
    const data = JSON.parse(text) as { detail?: unknown; error?: unknown };
    const message =
      (typeof data.detail === 'string' && data.detail) ||
      (typeof data.error === 'string' && data.error) ||
      response.statusText ||
      `HTTP ${response.status}`;
    return { message, payload: data };
  } catch {
    return { message: text, payload: text };
  }
}

// 登录端点的 401 表示"用户名或密码错误"，应交给调用方在表单上展示，
// 不能触发全局清 token + 跳转，否则会把用户从 /login 又踢回 /login。
const AUTH_ENDPOINTS_BYPASS_401 = new Set(['/api/auth/login']);

function isAuthBypassPath(path: string): boolean {
  return AUTH_ENDPOINTS_BYPASS_401.has(path);
}

// 401 全局处理：清空本地登录态并跳转到 /login。
// 用 location.assign 而不是 router.navigate，是为了：
//   1) lib 文件不依赖 react-router hooks（hook 只能在组件里用）；
//   2) 鉴权失效时整页 reload 是干净的副作用——所有内存中的 store 状态都该被丢弃。
// 已经在 /login 页时不重复跳，避免 reload 循环。
let unauthorizedHandled = false;
function handleUnauthorized(): void {
  if (typeof window === 'undefined') return;
  if (unauthorizedHandled) return;
  unauthorizedHandled = true;
  clearToken();
  clearUser();
  const path = window.location.pathname;
  if (path === '/login' || path === '/m/login') return;
  window.location.assign(path.startsWith('/m') ? '/m/login' : '/login');
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, signal } = options;
  const headers: Record<string, string> = {};

  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  const response = await fetch(buildUrl(path, query), {
    method,
    headers,
    body: payload,
    signal,
    credentials: 'same-origin',
  });

  if (response.status === 204) return undefined as T;

  if (!response.ok) {
    const { message, payload: errorPayload } = await parseError(response);
    if (response.status === 401 && !isAuthBypassPath(path)) {
      handleUnauthorized();
    }
    throw new ApiError(message, response.status, errorPayload);
  }

  // 兼容空响应体
  const text = await response.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

// --- Auth -----------------------------------------------------------------

export interface AuthSession {
  token: string;
  user: { username: string; is_admin: boolean };
}

/**
 * POST /api/auth/login
 * 请求体：{ username: string; password: string }
 * 响应体：{ token: string; user: { username: string } }
 * 失败：401 + { detail: '用户名或密码错误' }
 */
export async function login(input: { username: string; password: string }): Promise<AuthSession> {
  return request<AuthSession>('/api/auth/login', { method: 'POST', body: input });
}

/**
 * GET /api/auth/me
 * 响应体：{ username: string; is_admin: boolean }
 * 失败：401（token 失效或缺失），调用方应清空本地 token。
 */
export async function me(): Promise<{ username: string; is_admin: boolean }> {
  return request<{ username: string; is_admin: boolean }>('/api/auth/me');
}

// --- Apps -----------------------------------------------------------------

/**
 * GET /api/apps
 * 响应体：App[]，按 updated_at 倒序。
 * 仅返回当前登录用户拥有的应用。
 */
export async function listMyApps(): Promise<App[]> {
  return request<App[]>('/api/apps');
}

/**
 * GET /api/apps?market=true
 * 响应体：App[] 应用市场列表，包括公开已发布应用，以及当前用户自己的私有已发布应用。
 */
export async function listMarket(): Promise<App[]> {
  return request<App[]>('/api/apps', { query: { market: true } });
}

/**
 * GET /api/apps?gallery=true
 * 响应体：App[] 系统内置模板应用列表。
 */
export async function listTemplates(): Promise<App[]> {
  return request<App[]>('/api/apps', { query: { gallery: true } });
}

/**
 * GET /api/apps/recent-runs
 * 响应体：当前用户最近运行过的可见应用，按最近 run 倒序。
 */
export async function listRecentRuns(limit = 8): Promise<App[]> {
  return request<App[]>('/api/apps/recent-runs', { query: { limit } });
}

/**
 * POST /api/apps
 * 请求体：{ name?: string; description?: string }
 * 响应体：App（status='draft'，graph 为空 { nodes: [], execution_edges: [] }）
 * 默认名称由后端决定，前端建议传 '未命名 Mira 应用'。
 */
export async function createApp(payload: { name?: string; description?: string }): Promise<App> {
  return request<App>('/api/apps', { method: 'POST', body: payload });
}

/**
 * GET /api/apps/:id
 * 响应体：App
 * 失败：404 + { detail: '找不到应用 ${id}' }
 */
export async function getApp(id: string): Promise<App> {
  return request<App>(`/api/apps/${id}`);
}

/**
 * PATCH /api/apps/:id
 * 请求体：草稿元信息或 graph；发布状态与市场设置必须走 publish/unpublish。
 * 响应体：App（updated_at 由后端刷新；id 不可改）。
 */
export type AppPatchPayload = Pick<Partial<App>, 'name' | 'description' | 'cover' | 'graph'>;

function sanitizeAppPatchPayload(patch: AppPatchPayload): AppPatchPayload {
  if (!patch.graph) return patch;
  return { ...patch, graph: sanitizeGraphForApi(patch.graph) };
}

function sanitizeGraphForApi(graph: Graph): Graph {
  return {
    ...graph,
    nodes: graph.nodes.map((node) => {
      if (node.type !== 'generate' || !node.output_contract) return node;
      const output_contract = sanitizeOutputContract(node.output_contract);
      return { ...node, output_contract };
    }),
  };
}

type LooseOutputContract = NodeOutputContract & Record<string, unknown>;

function sanitizeOutputContract(contract: NodeOutputContract): NodeOutputContract {
  const loose = contract as LooseOutputContract;
  if (contract.type === 'json') {
    const {
      artifact_kind: _artifact_kind,
      max_count: _max_count,
      validate_office_documents: _validate_office_documents,
      ...cleaned
    } = loose;
    return cleaned as NodeOutputContract;
  }
  if (contract.type === 'html') {
    const {
      json_schema: _json_schema,
      artifact_kind: _artifact_kind,
      max_count: _max_count,
      validate_office_documents: _validate_office_documents,
      ...cleaned
    } = loose;
    return cleaned as NodeOutputContract;
  }
  if (contract.type === 'artifact') {
    const { json_schema: _json_schema, ...cleaned } = loose;
    return cleaned as NodeOutputContract;
  }
  return contract;
}

export async function patchApp(id: string, patch: AppPatchPayload): Promise<App> {
  return request<App>(`/api/apps/${id}`, { method: 'PATCH', body: sanitizeAppPatchPayload(patch) });
}

/**
 * DELETE /api/apps/:id
 * 响应：204 No Content
 * 业务约束：连带删除该 app 的 versions 与 runs（由后端在事务内处理）。
 */
export async function deleteApp(id: string): Promise<void> {
  await request<void>(`/api/apps/${id}`, { method: 'DELETE' });
}

/**
 * POST /api/apps/:id/clone
 * 响应体：App（新 id；name 后追加“（副本）”；status 重置为 draft）
 */
export async function cloneApp(id: string): Promise<App> {
  return request<App>(`/api/apps/${id}/clone`, { method: 'POST' });
}

/**
 * POST /api/apps/clone/:template_id
 * 响应体：App（同一用户同一模板重复调用时返回已有导入；首次导入 name 追加 “ Remix”；
 *   status 重置为 draft）
 * 失败：404 + { detail: '找不到模板 ${template_id}' }
 */
export async function cloneFromGallery(templateId: string): Promise<App> {
  return request<App>(`/api/apps/clone/${templateId}`, { method: 'POST' });
}

// --- Versions -------------------------------------------------------------

/**
 * GET /api/apps/:id/versions
 * 响应体：AppVersion[]，按 created_at 倒序。
 * 业务约束：单 app 版本上限建议 50（VERSION_LIMIT），超出后裁剪最旧的非 published 快照。
 */
export async function listVersions(appId: string): Promise<AppVersion[]> {
  return request<AppVersion[]>(`/api/apps/${appId}/versions`);
}

/**
 * POST /api/apps/:id/versions
 * 请求体：{ label?: string }（手动快照，label 为空时不写入）
 * 响应体：AppVersion（is_published 默认 false；快照内容来自当前 app.graph + name + description）
 */
export async function createVersion(appId: string, label?: string): Promise<AppVersion> {
  return request<AppVersion>(`/api/apps/${appId}/versions`, {
    method: 'POST',
    body: { label: label?.trim() || undefined },
  });
}

/**
 * POST /api/apps/:id/publish
 * 响应体：{ app: App; version: AppVersion }
 * 业务约束：
 *   - app.status 置为 'published'，published_at 更新为当前时间；
 *   - 自动生成一条 AppVersion（is_published=true，label 形如 '已发布 vN'，N=已存在的 published 数 + 1）。
 */
export async function publishApp(
  appId: string,
  payload?: { visibility?: App['visibility']; market_access?: App['market_access'] },
): Promise<{ app: App; version: AppVersion }> {
  return request<{ app: App; version: AppVersion }>(`/api/apps/${appId}/publish`, {
    method: 'POST',
    body: payload,
  });
}

/**
 * POST /api/apps/:id/lint
 * 请求体：{ graph?: Graph }
 * 响应体：WorkflowLintResult。error 阻断运行/发布，warning 仅提示。
 */
export async function lintAppGraph(
  appId: string,
  graph?: App['graph'],
  signal?: AbortSignal,
): Promise<WorkflowLintResult> {
  return request<WorkflowLintResult>(`/api/apps/${appId}/lint`, {
    method: 'POST',
    body: graph ? { graph: sanitizeGraphForApi(graph) } : {},
    signal,
  });
}

/**
 * POST /api/apps/:id/unpublish
 * 响应体：App（status 置回 'draft'，已存在的 published 版本不删除）
 */
export async function unpublishApp(appId: string): Promise<App> {
  return request<App>(`/api/apps/${appId}/unpublish`, { method: 'POST' });
}

/**
 * POST /api/versions/:id/clone
 * 响应体：App（基于版本快照创建新草稿应用；name 追加 “（vN）”，N 为该版本在所属 app 中的序号）
 */
export async function cloneFromVersion(versionId: string): Promise<App> {
  return request<App>(`/api/versions/${versionId}/clone`, { method: 'POST' });
}

// --- NL Compile -----------------------------------------------------------

/**
 * POST /api/nlcompile
 * 请求体：{ app_id: string; instruction: string; current_graph: Graph }
 * 响应体：planned plan 或 waiting_for_user decision request。
 *
 * 业务约束：
 *   - Codex 编译失败时返回 502，不做关键词识别。
 *   - plan_markdown 为后端生成的可读方案文档，前端用于弹窗确认渲染。
 *
 * applied_patches 元素形状（参见 types.ts GraphPatch）：
 *   { op: 'add_node', node }
 *   { op: 'remove_node', id }
 *   { op: 'update_node', id, patch }
 *   { op: 'add_edge', edge }
 *   { op: 'remove_edge', id }
 */
export type NlCompileResponse =
  | {
      status: 'planned';
      compile_id: string;
      plan: NlCompilePlan;
      plan_markdown: string;
    }
  | {
      status: 'completed';
      new_graph: App['graph'];
      applied_patches: GraphPatch[];
      warnings?: string[];
      plan_markdown: string;
    }
  | {
      status: 'waiting_for_user';
      compile_id: string;
      request: RunWaitingRequest;
    }
  | {
      status: 'planning' | 'applying' | 'interrupted';
      compile_id: string;
      instruction?: string | null;
      request?: RunWaitingRequest | null;
      plan?: NlCompilePlan | null;
      plan_markdown?: string | null;
      error?: string | null;
    };

export async function nlCompile(input: {
  app_id: string;
  instruction: string;
  current_graph: App['graph'];
  compile_id?: string;
  attachments?: { id: string; name?: string }[];
}, signal?: AbortSignal): Promise<NlCompileResponse> {
  return request<NlCompileResponse>('/api/nlcompile', { method: 'POST', body: input, signal });
}

export async function cancelNlCompile(compileId: string): Promise<void> {
  await request<void>(`/api/nlcompile/${compileId}/cancel`, { method: 'POST' });
}

export async function getActiveNlCompile(appId: string, signal?: AbortSignal): Promise<NlCompileResponse | null> {
  try {
    return (await request<NlCompileResponse | undefined>(`/api/apps/${appId}/nlcompile/active`, { signal })) ?? null;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function applyNlCompile(compileId: string, signal?: AbortSignal): Promise<NlCompileResponse> {
  return request<NlCompileResponse>(`/api/nlcompile/${compileId}/apply`, { method: 'POST', signal });
}

export async function refineNlCompile(
  compileId: string,
  feedback: string,
  signal?: AbortSignal,
): Promise<NlCompileResponse> {
  return request<NlCompileResponse>(`/api/nlcompile/${compileId}/refine`, {
    method: 'POST',
    body: { feedback },
    signal,
  });
}

export async function resumeNlCompile(
  compileId: string,
  payload: {
    answers: DecisionAnswer[];
    text?: string | null;
    attachments?: { id: string; name?: string }[];
  },
  signal?: AbortSignal,
): Promise<NlCompileResponse> {
  return request<NlCompileResponse>(`/api/nlcompile/${compileId}/resume`, {
    method: 'POST',
    body: payload,
    signal,
  });
}

// --- Graph Layout ---------------------------------------------------------

export async function beautifyGraphLayout(input: {
  app_id: string;
  graph: App['graph'];
  node_sizes?: GraphNodeSizeMap;
}, signal?: AbortSignal): Promise<{ graph: App['graph'] }> {
  return request<{ graph: App['graph'] }>('/api/graph-layout/beautify', {
    method: 'POST',
    body: input,
    signal,
  });
}

// --- Prompt Assistant -----------------------------------------------------

export async function generatePromptAssistant(input: {
  app_id: string;
  generation_id?: string;
  graph: App['graph'];
  node_id: string;
  user_request: string;
  model?: string;
  reasoning_effort?: ReasoningEffort;
}, signal?: AbortSignal): Promise<PromptAssistantResponse> {
  return request<PromptAssistantResponse>('/api/prompt-assistant/generate', { method: 'POST', body: input, signal });
}

type PromptAssistantOutputContract = Omit<NodeOutputContract, 'validate_office_documents'> & {
  validate_office_documents?: boolean | null;
};

export type PromptAssistantResponse =
  | {
      status: 'completed';
      prompt: string;
      output_contract?: PromptAssistantOutputContract | null;
    }
  | {
      status: 'waiting_for_user';
      generation_id: string;
      request: RunWaitingRequest;
    }
  | {
      status: 'interrupted';
      generation_id: string;
      error: string;
    };

export async function resumePromptAssistant(
  generationId: string,
  payload: {
    answers: DecisionAnswer[];
    text?: string | null;
    attachments?: { id: string; name?: string }[];
  },
  signal?: AbortSignal,
): Promise<PromptAssistantResponse> {
  return request<PromptAssistantResponse>(`/api/prompt-assistant/${generationId}/resume`, {
    method: 'POST',
    body: payload,
    signal,
  });
}

export async function cancelPromptAssistant(generationId: string): Promise<void> {
  await request<void>(`/api/prompt-assistant/${generationId}/cancel`, { method: 'POST' });
}

// --- Runs -----------------------------------------------------------------

/**
 * POST /api/runs
 * 请求体：{ app_id: string; inputs: Record<string, unknown> }
 *   inputs 的 key 是 user_input 节点的 id，value 是用户填写的字符串/文件 dataURL 等。
 * 响应体：{ run_id: string; graph: Graph }，graph 是本次 run 的执行快照。
 * 业务约束：
 *   - 后端创建 Run 记录（status='pending'，保存 graph 快照，steps 按快照节点生成占位）；
 *   - 真正的执行通过 SSE 推送（参见 lib/ws.ts），返回 run_id 供前端打开事件流。
 */
export async function createRun(payload: {
  app_id: string;
  inputs: Record<string, unknown>;
}): Promise<{ run_id: string; graph: Run['graph'] }> {
  return request<{ run_id: string; graph: Run['graph'] }>('/api/runs', { method: 'POST', body: payload });
}

/**
 * POST /api/runs/:id/rerun-from
 * 基于历史 run 的节点前 checkpoint 创建新 run；cut 前状态冻结，使用当前 App graph 执行指定节点及下游。
 */
export async function rerunFrom(
  id: string,
  payload: {
    app_id: string;
    node_id: string;
    inputs?: Record<string, unknown>;
    condition_branch_override?: ConditionBranchOverride;
  },
): Promise<{ run_id: string; graph: Run['graph'] }> {
  return request<{ run_id: string; graph: Run['graph'] }>(`/api/runs/${id}/rerun-from`, {
    method: 'POST',
    body: payload,
  });
}

/**
 * GET /api/runs/:id
 * 响应体：Run（包含完整 steps 与 logs，便于历史回放）。
 */
export async function getRun(id: string): Promise<Run> {
  return request<Run>(`/api/runs/${id}`);
}

/**
 * GET /api/runs/:id/steps/:node_id/trace
 * 响应体：LLM 节点执行 Trace（实际 prompt、raw chunks、最终输出和 artifacts）。
 */
export async function getRunStepTrace(id: string, nodeId: string): Promise<RunStepTrace> {
  return request<RunStepTrace>(`/api/runs/${id}/steps/${nodeId}/trace`);
}

/**
 * GET /api/runs/:id/artifacts
 * 响应体：当前 run 的一等文件产物列表，来源于工作区文件和 artifact 输出契约。
 */
export async function listRunArtifacts(id: string): Promise<RunArtifactsOut> {
  return request<RunArtifactsOut>(`/api/runs/${id}/artifacts`);
}

/**
 * PATCH /api/runs/:id
 * 请求体：{ name: string }
 * 响应体：Run（更新后的运行记录）。
 */
export async function patchRun(id: string, payload: { name: string }): Promise<Run> {
  return request<Run>(`/api/runs/${id}`, { method: 'PATCH', body: payload });
}

/**
 * POST /api/runs/:id/cancel
 * 响应：204 No Content
 * 业务约束：仅在可取消运行态生效；前端取消后会 GET run 快照兜底刷新 cancelled 状态。
 */
export async function cancelRun(id: string): Promise<void> {
  await request<void>(`/api/runs/${id}/cancel`, { method: 'POST' });
}

/**
 * POST /api/runs/:id/continue
 * 响应体：Run。仅 interrupted run 可继续，后续进展由 SSE 推送。
 */
export async function continueRun(id: string): Promise<Run> {
  return request<Run>(`/api/runs/${id}/continue`, { method: 'POST' });
}

/**
 * GET /api/apps/:id/runs/summary?limit=50
 * 响应体：轻量 RunSummary[]，用于历史列表；点击条目后再 GET /api/runs/:id。
 */
export async function listRunSummaries(appId: string, limit = 50): Promise<RunSummary[]> {
  return request<RunSummary[]>(`/api/apps/${appId}/runs/summary`, { query: { limit } });
}

/**
 * DELETE /api/runs/:id
 * 响应：204 No Content
 */
export async function deleteRun(id: string): Promise<void> {
  await request<void>(`/api/runs/${id}`, { method: 'DELETE' });
}

/**
 * POST /api/runs/:id/resume （ask_user 中段交互）
 * 请求体：{ node_id, tool_use_id, answers, text?, attachments? }
 * 响应：204 No Content；真正的运行进展由 SSE 续推。
 *
 * 错误码：
 *   404 运行记录不存在 / 附件不存在
 *   409 当前没有等待该节点的输入 / ask_user 已失效
 *   400 选项不合法 / 必须至少提供一项输入 / 补充文本过长
 */
export async function resumeRun(
  id: string,
  payload: {
    node_id: string;
    tool_use_id: string;
    answers?: DecisionAnswer[];
    text?: string | null;
    attachments?: { id: string; name?: string }[];
  },
): Promise<void> {
  await request<void>(`/api/runs/${id}/resume`, { method: 'POST', body: payload });
}

// --- Settings -------------------------------------------------------------

/**
 * GET /api/settings
 * 响应体：MiraSettings（全局共享，普通用户只读）。
 * 普通用户返回的 mcp_servers[].headers[].value 会被脱敏为空字符串；管理员返回完整值。
 *
 * supported_models 是 Codex 可选模型的全局列表。
 */
export async function getSettings(): Promise<MiraSettings> {
  return request<MiraSettings>('/api/settings');
}

/**
 * PATCH /api/settings/skills/:skill_id
 * 请求体：{ enabled?: boolean, planning_enabled?: boolean }，单条更新 Skill 状态。
 * 响应体：MiraSettings。
 */
export async function updateSkillEnabled(skillId: string, enabled: boolean): Promise<MiraSettings> {
  return request<MiraSettings>(`/api/settings/skills/${skillId}`, { method: 'PATCH', body: { enabled } });
}

export async function updateSkillPlanningEnabled(skillId: string, planningEnabled: boolean): Promise<MiraSettings> {
  return request<MiraSettings>(`/api/settings/skills/${skillId}`, {
    method: 'PATCH',
    body: { planning_enabled: planningEnabled },
  });
}

/**
 * DELETE /api/settings/skills/:skill_id
 * 响应：204 No Content。
 */
export async function deleteSkill(skillId: string): Promise<void> {
  await request<void>(`/api/settings/skills/${skillId}`, { method: 'DELETE' });
}

/**
 * GET /api/settings/skills/:skill_id/skill-md
 * 响应体：已上传 Skill zip 内的 SKILL.md 原文；仅管理员可读。
 */
export async function getSkillMarkdown(skillId: string): Promise<SkillMarkdown> {
  return request<SkillMarkdown>(`/api/settings/skills/${skillId}/skill-md`);
}

/**
 * POST /api/settings/mcp
 * 请求体：McpServerConfig（含前端生成的 id）。
 * 响应体：MiraSettings。
 */
export async function addMcpServer(server: McpServerConfig): Promise<MiraSettings> {
  return request<MiraSettings>('/api/settings/mcp', { method: 'POST', body: server });
}

/**
 * PUT /api/settings/mcp/:server_id
 * 请求体：McpServerConfig（id 必须与路径一致）。
 * 响应体：MiraSettings。
 */
export async function updateMcpServer(serverId: string, server: McpServerConfig): Promise<MiraSettings> {
  return request<MiraSettings>(`/api/settings/mcp/${serverId}`, { method: 'PUT', body: server });
}

/**
 * DELETE /api/settings/mcp/:server_id
 * 响应：204 No Content。
 */
export async function deleteMcpServer(serverId: string): Promise<void> {
  await request<void>(`/api/settings/mcp/${serverId}`, { method: 'DELETE' });
}

/**
 * GET /api/settings/codex/config
 * 响应体：CodexConfigFile；content/auth 由后端从 DB 解密返回。
 */
export async function getCodexConfig(): Promise<CodexConfigFile> {
  return request<CodexConfigFile>('/api/settings/codex/config');
}

/**
 * GET /api/settings/codex/setup-state
 * 响应体：{ completed: boolean }。
 * 仅管理员可调。completed=true 表示已同时保存过 Codex config.toml 与 auth.json。
 */
export async function getCodexSetupState(): Promise<CodexSetupState> {
  return request<CodexSetupState>('/api/settings/codex/setup-state');
}

/**
 * PUT /api/settings/codex/config
 * 请求体：{ content, auth_content, supported_models }。
 */
export async function saveCodexConfig(
  content: string,
  options: { authContent: string; supportedModels: string[] },
): Promise<CodexConfigFile> {
  const body: Record<string, unknown> = { content, supported_models: options.supportedModels };
  body.auth_content = options.authContent;
  return request<CodexConfigFile>('/api/settings/codex/config', {
    method: 'PUT',
    body,
  });
}

/**
 * POST /api/settings/codex/status
 * 响应体：CodexStatus（实时探测 App Server / 配置，并执行一次真实短调用确认可用；不写 DB）。
 * 仅管理员可调。
 */
export async function refreshCodexStatus(): Promise<CodexStatus> {
  return request<CodexStatus>('/api/settings/codex/status', { method: 'POST' });
}

/**
 * GET /api/settings/instructions
 */
export async function getInstructionFile(): Promise<InstructionFile> {
  return request<InstructionFile>('/api/settings/instructions');
}

/**
 * PUT /api/settings/instructions
 * 请求体：{ content: string }
 * 仅管理员可调。内容按 plain text 原样保存。
 */
export async function saveInstructionFile(content: string): Promise<InstructionFile> {
  return request<InstructionFile>('/api/settings/instructions', {
    method: 'PUT',
    body: { content },
  });
}

/**
 * GET /api/settings/prompts
 * 响应体：PromptTemplate[]。仅管理员可调。
 */
export async function getPromptTemplates(): Promise<PromptTemplate[]> {
  return request<PromptTemplate[]>('/api/settings/prompts');
}

/**
 * PUT /api/settings/prompts/:key
 * 请求体：{ content: string }。仅管理员可调。
 */
export async function savePromptTemplate(key: string, content: string): Promise<PromptTemplate> {
  return request<PromptTemplate>(`/api/settings/prompts/${key}`, {
    method: 'PUT',
    body: { content },
  });
}

// --- Uploads --------------------------------------------------------------

export interface UploadOut {
  id: string;
  name: string;
  mime: string;
  size: number;
  created_at: string;
}

/**
 * POST /api/uploads
 * 请求体：multipart/form-data，字段名 `file`。
 * 响应体：UploadOut（id 可在 Run.inputs / resume payload 中复用）。
 */
export async function uploadFile(file: File): Promise<UploadOut> {
  const form = new FormData();
  form.append('file', file, file.name);
  return request<UploadOut>('/api/uploads', { method: 'POST', body: form });
}

/**
 * GET /api/uploads/:id
 * 响应体：原始文件 blob；仅当前用户自己的 upload 可读取。
 */
export async function fetchUploadBlob(uploadId: UploadRef['id'], signal?: AbortSignal): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const path = `/api/uploads/${encodeURIComponent(uploadId)}`;
  const response = await fetch(path, { method: 'GET', headers, credentials: 'same-origin', signal });
  if (!response.ok) {
    const { message, payload } = await parseError(response);
    if (response.status === 401) handleUnauthorized();
    throw new ApiError(message, response.status, payload);
  }
  return response.blob();
}

/**
 * GET /api/apps/:id/cover
 * 响应体：应用封面 blob；支持当前用户自己的应用和可见的应用市场应用。
 */
export async function fetchAppCoverBlob(appId: App['id'], signal?: AbortSignal): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const path = `/api/apps/${encodeURIComponent(appId)}/cover`;
  const response = await fetch(path, { method: 'GET', headers, credentials: 'same-origin', signal });
  if (!response.ok) {
    const { message, payload } = await parseError(response);
    if (response.status === 401) handleUnauthorized();
    throw new ApiError(message, response.status, payload);
  }
  return response.blob();
}

// --- Skills ---------------------------------------------------------------

/**
 * POST /api/skills/parse
 * 请求体：multipart/form-data，字段名 `archive`，单个 .zip 文件，建议上限 ≤10MB。
 * 响应体：SkillConfig（id 由后端生成，例如 'skill_xxx'；
 *   后端解析压缩包元数据后填入 name / description / archive_name / archive_size / uploaded_at；
 *   enabled 默认 true）。
 * 失败：400 + { detail: '只接受 .zip 压缩包' } 等。
 */
export async function parseSkillArchive(archive: File): Promise<SkillConfig> {
  const form = new FormData();
  form.append('archive', archive, archive.name);
  return request<SkillConfig>('/api/skills/parse', { method: 'POST', body: form });
}
