import type { Graph, WorkflowNode } from '../../types';
import type { PromptTokenDefinition } from './promptTokens';

export interface PromptFieldDefinition extends PromptTokenDefinition {
  kind: 'field' | 'enum';
  sourceNodeId: string;
  sourceLabel: string;
  scope: 'upstream' | 'output';
  description?: string;
}

export function buildPromptFieldTokens(graph: Graph | undefined, nodeId: string): PromptFieldDefinition[] {
  if (!graph) return [];
  const currentNode = graph.nodes.find((node) => node.id === nodeId);
  if (!currentNode) return [];

  const tokens: PromptFieldDefinition[] = [];
  const seenSources = new Set<string>();
  for (const edge of graph.edges) {
    if (edge.target !== nodeId || seenSources.has(edge.source)) continue;
    seenSources.add(edge.source);
    const source = graph.nodes.find((node) => node.id === edge.source);
    if (source) appendNodeSchemaTokens(tokens, source, 'upstream');
  }
  appendNodeSchemaTokens(tokens, currentNode, 'output');
  return tokens;
}

export function mergePromptFieldTokens(fields: PromptFieldDefinition[]): PromptTokenDefinition[] {
  const merged = new Map<string, PromptTokenDefinition>();
  for (const field of fields) {
    const current = merged.get(field.value);
    if (!current) {
      merged.set(field.value, { ...field });
      continue;
    }
    if (current.label !== field.label) current.label = field.value;
    if (current.description !== field.description) current.description = undefined;
  }
  return [...merged.values()].sort((a, b) => b.value.length - a.value.length || a.value.localeCompare(b.value));
}

export function promptFieldOptionLabel(field: PromptFieldDefinition): string {
  const scope = field.scope === 'upstream' ? '上游' : '当前输出';
  return `${scope} · ${field.sourceLabel} · ${field.label}`;
}

function appendNodeSchemaTokens(
  tokens: PromptFieldDefinition[],
  node: WorkflowNode,
  scope: PromptFieldDefinition['scope'],
): void {
  const schema = jsonSchemaForNode(node);
  if (!schema || !isRecord(schema.properties)) return;
  appendProperties(tokens, schema.properties, '', {
    sourceNodeId: node.id,
    sourceLabel: node.title?.trim() || node.id,
    scope,
  });
}

function appendProperties(
  tokens: PromptFieldDefinition[],
  properties: Record<string, unknown>,
  parentPath: string,
  source: Pick<PromptFieldDefinition, 'sourceNodeId' | 'sourceLabel' | 'scope'>,
): void {
  for (const [name, rawSchema] of Object.entries(properties)) {
    if (!isRecord(rawSchema)) continue;
    const path = parentPath ? `${parentPath}.${name}` : name;
    const title = nonEmptyString(rawSchema.title);
    const description = nonEmptyString(rawSchema.description);
    tokens.push({
      ...source,
      value: path,
      label: title ? `${title} · ${path}` : path,
      kind: 'field',
      description,
    });
    if (Array.isArray(rawSchema.enum) && rawSchema.enum.length <= 20) {
      for (const enumValue of rawSchema.enum) {
        if (!isScalar(enumValue)) continue;
        const displayValue = formatEnumValue(enumValue);
        const value = `${path}=${displayValue}`;
        tokens.push({
          ...source,
          value,
          label: title ? `${title}：${displayValue} · ${value}` : value,
          kind: 'enum',
          description,
        });
      }
    }
    if (rawSchema.type === 'object' && isRecord(rawSchema.properties)) {
      appendProperties(tokens, rawSchema.properties, path, source);
    }
    if (rawSchema.type === 'array' && isRecord(rawSchema.items)) {
      const items = rawSchema.items;
      if (items.type === 'object' && isRecord(items.properties)) {
        appendProperties(tokens, items.properties, `${path}[]`, source);
      }
    }
  }
}

function jsonSchemaForNode(node: WorkflowNode): Record<string, unknown> | null {
  if (node.type !== 'generate' || node.output_contract?.type !== 'json') return null;
  return isRecord(node.output_contract.json_schema) ? node.output_contract.json_schema : null;
}

function formatEnumValue(value: string | number | boolean | null): string {
  if (typeof value === 'string' && /^[\p{L}\p{N}_-]+$/u.test(value)) return value;
  return JSON.stringify(value);
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function isScalar(value: unknown): value is string | number | boolean | null {
  return value === null || ['string', 'number', 'boolean'].includes(typeof value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
