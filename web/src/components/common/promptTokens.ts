import type { ToolConfig } from '../../types';

export type PromptTokenKind = 'system' | 'skill' | 'mcp' | 'field' | 'enum';
export type PromptToolTokenKind = Extract<PromptTokenKind, 'system' | 'skill' | 'mcp'>;

export interface PromptTokenDefinition {
  value: string;
  label: string;
  kind: PromptTokenKind;
  description?: string;
}

export interface PromptToolTokenDefinition extends PromptTokenDefinition {
  kind: PromptToolTokenKind;
}

const SYSTEM_TOKENS: PromptToolTokenDefinition[] = [
  { value: 'ask_user', label: '询问用户', kind: 'system' },
];

const TOOL_LABELS: Record<string, string> = {
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

export function buildPromptTokens(tools: ToolConfig[], includeSystem: boolean): PromptToolTokenDefinition[] {
  const tokens = new Map<string, PromptToolTokenDefinition>();
  if (includeSystem) {
    for (const token of SYSTEM_TOKENS) tokens.set(token.value, token);
  }
  for (const tool of tools) {
    const name = tool.name.trim();
    if (!name || tokens.has(name)) continue;
    const kind = tool.id.startsWith('mcp:') ? 'mcp' : 'skill';
    tokens.set(name, {
      value: name,
      label: TOOL_LABELS[name] ?? name,
      kind,
    });
  }
  return [...tokens.values()].sort((a, b) => b.value.length - a.value.length || a.value.localeCompare(b.value));
}

export function promptTokenOptionLabel(token: PromptToolTokenDefinition): string {
  const kindLabel = token.kind === 'system' ? '系统' : token.kind === 'skill' ? 'Skill' : 'MCP';
  return `${kindLabel} · ${token.label}`;
}
