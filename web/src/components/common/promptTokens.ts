import type { Graph, ToolConfig } from '../../types';

export interface PromptTokenDefinition {
  value: string;
  label: string;
  kind: 'tool' | 'field';
}

const TOOL_LABELS: Record<string, string> = {
  ask_user: '询问用户',
  'be-serious': '严谨写作',
  'design-md': '视觉规范',
  docx: 'Word 文档',
  'find-skills': '查找工具',
  'frontend-design': '前端设计',
  pdf: 'PDF 文档',
  playwright: '浏览器验收',
  pptx: '演示文稿',
  xlsx: '电子表格',
};

const FIELD_LABELS: Record<string, string> = {
  ask_user_enabled: '允许询问用户',
  artifact_kind: '产物类型',
  audit_status: '验收状态',
  failure_kind: '失败类型',
  hard_exclusions: '硬性排除项',
  json_schema: 'JSON 结构',
  known_versions_resolved: '已知版本已确认',
  no_login_placeholders: '无登录占位项',
  not_ready: '未就绪',
  office_and_archive_clean: 'Office 与归档合规',
  outcome: '处理结果',
  output_contract: '输出契约',
  package_inventory: '交付包清单',
  product_failed: '产品验收失败',
  product_gate_passed: '产品门禁通过',
  ready_for_delivery: '可交付',
  ready_for_documents: '可生成材料',
  route: '路由',
  scope_alignment: '范围一致',
  screenshot_provenance_complete: '截图溯源完整',
  source_handle: '分支标识',
  source_material_consistent: '源码材料一致',
  source_node_id: '主输入节点',
  spec_match: '规格一致',
  template_structure_preserved: '模板结构已保留',
  validate_office_documents: '校验 Office 文档',
};

const FIELD_WORD_LABELS: Record<string, string> = {
  acceptance: '验收',
  actions: '动作',
  alignment: '一致',
  archive: '归档',
  audit: '验收',
  complete: '完整',
  consistent: '一致',
  contract: '契约',
  delivery: '交付',
  documents: '材料',
  exclusions: '排除项',
  failed: '失败',
  for: '可用于',
  gate: '门禁',
  hard: '硬性',
  input: '输入',
  inventory: '清单',
  issues: '问题',
  known: '已知',
  login: '登录',
  material: '材料',
  match: '一致',
  no: '无',
  node: '节点',
  not: '未',
  office: 'Office',
  outcome: '结果',
  output: '输出',
  package: '交付包',
  passed: '通过',
  placeholders: '占位项',
  preserved: '已保留',
  product: '产品',
  provenance: '溯源',
  ready: '就绪',
  requires: '需要',
  resolved: '已确认',
  route: '路由',
  schema: '结构',
  scope: '范围',
  screenshot: '截图',
  source: '来源',
  spec: '规格',
  status: '状态',
  structure: '结构',
  template: '模板',
  user: '用户',
  versions: '版本',
};

const STRUCTURED_IDENTIFIER_PATTERN = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g;

export function buildPromptTokens(graph: Graph | undefined, tools: ToolConfig[], prompt: string): PromptTokenDefinition[] {
  const tokens = new Map<string, PromptTokenDefinition>();
  addToken(tokens, { value: 'ask_user', label: TOOL_LABELS.ask_user, kind: 'tool' });

  for (const tool of tools) {
    if (!tool.name.trim()) continue;
    addToken(tokens, {
      value: tool.name,
      label: TOOL_LABELS[tool.name] ?? `工具 · ${tool.name}`,
      kind: 'tool',
    });
  }

  const schemaFields = collectSchemaFields(graph);
  for (const field of schemaFields) {
    addToken(tokens, { value: field, label: fieldLabel(field), kind: 'field' });
  }

  for (const match of prompt.matchAll(STRUCTURED_IDENTIFIER_PATTERN)) {
    const field = match[0];
    if (field === 'ask_user') continue;
    addToken(tokens, { value: field, label: fieldLabel(field), kind: 'field' });
  }

  return [...tokens.values()].sort((a, b) => b.value.length - a.value.length || a.value.localeCompare(b.value));
}

function addToken(tokens: Map<string, PromptTokenDefinition>, token: PromptTokenDefinition): void {
  if (!tokens.has(token.value)) tokens.set(token.value, token);
}

function collectSchemaFields(graph: Graph | undefined): Set<string> {
  const fields = new Set<string>();
  if (!graph) return fields;
  for (const node of graph.nodes) {
    if (node.type !== 'generate' || node.output_contract?.type !== 'json') continue;
    collectProperties(node.output_contract.json_schema, fields);
  }
  return fields;
}

function collectProperties(value: unknown, fields: Set<string>): void {
  if (Array.isArray(value)) {
    for (const item of value) collectProperties(item, fields);
    return;
  }
  if (!isRecord(value)) return;
  if (isRecord(value.properties)) {
    for (const field of Object.keys(value.properties)) fields.add(field);
  }
  for (const nested of Object.values(value)) collectProperties(nested, fields);
}

function fieldLabel(field: string): string {
  const explicit = FIELD_LABELS[field];
  if (explicit) return explicit;
  const words = field.split('_');
  const translated = words.map((word) => FIELD_WORD_LABELS[word]);
  if (translated.every(Boolean)) return translated.join('');
  return `系统字段 · ${field}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
