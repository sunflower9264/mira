import { useMemo, useState } from 'react';
import type { Graph, NodeOutputContract, WorkflowNode } from '../../types';
import { useEditorStore } from '../../stores/useEditorStore';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { PlusIcon, SparkleIcon, TrashIcon } from '../common/Icons';

interface ResultOutlineEditorProps {
  nodeId: string;
  contract: NodeOutputContract | undefined;
  onChange(next: NodeOutputContract): void;
  onTextChange(next: NodeOutputContract): void;
  onInfer(): void;
  inferring: boolean;
}

interface PendingDelete {
  title: string;
  dependents: string[];
}

export function ResultOutlineEditor({
  nodeId,
  contract,
  onChange,
  onTextChange,
  onInfer,
  inferring,
}: ResultOutlineEditorProps) {
  const graph = useEditorStore((state) => state.app?.graph);
  const schema = useMemo(
    () => (contract?.type === 'json' && isRecord(contract.json_schema)
      ? contract.json_schema
      : defaultReferenceableSchema()),
    [contract],
  );
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null);
  const topLevelCount = Object.keys(schemaProperties(schema)).length;

  if (contract?.type !== 'json') return null;

  const applySchema = (nextSchema: Record<string, unknown>, textChange = false) => {
    const nextContract: NodeOutputContract = { ...contract, type: 'json', json_schema: nextSchema };
    if (textChange) onTextChange(nextContract);
    else onChange(nextContract);
  };

  const updateItem = (
    path: string[],
    patch: (current: Record<string, unknown>) => Record<string, unknown>,
    textChange = false,
  ) => {
    applySchema(updatePropertyAtPath(schema, path, patch), textChange);
  };

  const requestDelete = (path: string[], title: string) => {
    const reference = referencePathFor(schema, path);
    const dependents = graph ? findDependentNodeLabels(graph, nodeId, reference) : [];
    if (dependents.length === 0) {
      applySchema(removePropertyAtPath(schema, path));
      return;
    }
    setPendingDelete({ title, dependents });
  };

  const addResult = () => {
    const properties = schemaProperties(schema);
    const key = nextResultKey(properties);
    const nextProperties = {
      ...properties,
      [key]: {
        title: '新内容',
        description: '说明这项结果包含什么。',
        type: 'string',
      },
    };
    applySchema(withProperties(schema, nextProperties));
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-black/[0.07] bg-[#FCFCFB]">
      <div className="flex items-start justify-between gap-3 border-b border-black/[0.06] px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-black/80">结果内容</h3>
            <span className="rounded-full bg-black/[0.05] px-2 py-0.5 text-[10px] font-medium text-black/45">
              {topLevelCount} 项
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-black/40">
            后续步骤可以单独引用这些内容，技术格式由系统维护。
          </p>
        </div>
        <button
          type="button"
          onClick={onInfer}
          disabled={inferring}
          className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-black/[0.08] bg-white px-3 text-xs font-medium text-black/60 transition hover:border-black/15 hover:text-black disabled:cursor-wait disabled:opacity-45"
        >
          <SparkleIcon className={`h-3.5 w-3.5 ${inferring ? 'animate-pulse' : ''}`} />
          {inferring ? '正在整理' : '根据提示词整理'}
        </button>
      </div>

      <div className="max-h-64 overflow-y-auto px-3 py-2">
        <ResultItems
          container={schema}
          pathPrefix={[]}
          depth={0}
          onUpdate={updateItem}
          onDelete={requestDelete}
        />
        <button
          type="button"
          onClick={addResult}
          className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium text-black/50 transition hover:bg-black/[0.04] hover:text-black/75"
        >
          <PlusIcon className="h-3.5 w-3.5" />
          添加一项结果
        </button>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => setPendingDelete(null)}
        title={`“${pendingDelete?.title ?? '这项内容'}”仍在被使用`}
        description={pendingDelete ? (
          <div className="space-y-2">
            <p>请先在以下步骤中移除对应引用，再回来删除这项内容：</p>
            <ul className="list-disc space-y-1 pl-5">
              {pendingDelete.dependents.map((label) => <li key={label}>{label}</li>)}
            </ul>
          </div>
        ) : undefined}
        confirmLabel="知道了"
        cancelLabel="返回"
      />
    </section>
  );
}

function ResultItems({
  container,
  pathPrefix,
  depth,
  onUpdate,
  onDelete,
}: {
  container: Record<string, unknown>;
  pathPrefix: string[];
  depth: number;
  onUpdate(
    path: string[],
    patch: (current: Record<string, unknown>) => Record<string, unknown>,
    textChange?: boolean,
  ): void;
  onDelete(path: string[], title: string): void;
}) {
  const properties = schemaProperties(container);
  const entries = Object.entries(properties).filter((entry): entry is [string, Record<string, unknown>] => isRecord(entry[1]));

  return (
    <div className={depth > 0 ? 'ml-4 border-l border-black/[0.08] pl-3' : ''}>
      {entries.map(([key, item], index) => {
        const path = [...pathPrefix, key];
        const title = nonEmptyString(item.title) ?? '未命名内容';
        const description = typeof item.description === 'string' ? item.description : '';
        const multiple = item.type === 'array';
        const children = childObjectSchema(item);
        const canDelete = entries.length > 1;
        return (
          <div key={path.join('\u0000')} className="group/result py-2">
            <div className="flex items-start gap-2.5">
              <div className="mt-1.5 grid h-5 w-5 shrink-0 place-items-center rounded-full bg-black/[0.055] text-[10px] font-semibold text-black/40">
                {index + 1}
              </div>
              <div className="min-w-0 flex-1">
                <input
                  value={typeof item.title === 'string' ? item.title : ''}
                  onChange={(event) => onUpdate(path, (current) => ({ ...current, title: event.target.value }), true)}
                  placeholder="这项内容叫什么"
                  aria-label={`${title}的名称`}
                  className="h-7 w-full min-w-0 bg-transparent text-sm font-medium text-black/75 outline-none placeholder:text-black/30 focus:text-black"
                />
                <input
                  value={description}
                  onChange={(event) => onUpdate(path, (current) => ({ ...current, description: event.target.value }), true)}
                  placeholder="简单说明它包含什么"
                  aria-label={`${title}的说明`}
                  className="h-6 w-full min-w-0 bg-transparent text-xs text-black/45 outline-none placeholder:text-black/25 focus:text-black/65"
                />
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <div className="flex rounded-full bg-black/[0.045] p-0.5" aria-label={`${title}的数量`}>
                  <button
                    type="button"
                    onClick={() => {
                      if (multiple) onUpdate(path, toSingleValue);
                    }}
                    className={`rounded-full px-2 py-1 text-[10px] font-medium transition ${
                      multiple ? 'text-black/35 hover:text-black/60' : 'bg-white text-black/65 shadow-sm'
                    }`}
                  >
                    一个
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (!multiple) onUpdate(path, toMultipleValues);
                    }}
                    className={`rounded-full px-2 py-1 text-[10px] font-medium transition ${
                      multiple ? 'bg-white text-black/65 shadow-sm' : 'text-black/35 hover:text-black/60'
                    }`}
                  >
                    多个
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => onDelete(path, title)}
                  disabled={!canDelete}
                  className="rounded-full p-1.5 text-black/25 opacity-40 transition hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-20 disabled:hover:bg-transparent disabled:hover:text-black/25 group-hover/result:opacity-100 focus:opacity-100"
                  aria-label={`删除${title}`}
                  title={canDelete ? `删除${title}` : '至少保留一项内容'}
                >
                  <TrashIcon className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            {children ? (
              <div className="mt-1">
                <div className="mb-0.5 ml-4 text-[10px] font-medium tracking-wide text-black/30">其中包含</div>
                <ResultItems
                  container={children}
                  pathPrefix={path}
                  depth={depth + 1}
                  onUpdate={onUpdate}
                  onDelete={onDelete}
                />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function defaultReferenceableSchema(): Record<string, unknown> {
  return {
    title: '可引用结果',
    description: '当前步骤中可以被后续步骤单独使用的内容。',
    type: 'object',
    additionalProperties: false,
    properties: {
      result: {
        title: '主要内容',
        description: '当前步骤生成的主要内容。',
        type: 'string',
      },
    },
    required: ['result'],
  };
}

function updatePropertyAtPath(
  container: Record<string, unknown>,
  path: string[],
  update: (current: Record<string, unknown>) => Record<string, unknown>,
): Record<string, unknown> {
  const [key, ...rest] = path;
  if (!key) return container;
  const properties = schemaProperties(container);
  const current = properties[key];
  if (!isRecord(current)) return container;
  const next = rest.length === 0
    ? update(current)
    : current.type === 'array' && isRecord(current.items)
      ? { ...current, items: updatePropertyAtPath(current.items, rest, update) }
      : updatePropertyAtPath(current, rest, update);
  return withProperties(container, { ...properties, [key]: next });
}

function removePropertyAtPath(container: Record<string, unknown>, path: string[]): Record<string, unknown> {
  const [key, ...rest] = path;
  if (!key) return container;
  const properties = schemaProperties(container);
  const current = properties[key];
  if (!isRecord(current)) return container;
  if (rest.length === 0) {
    const nextProperties = { ...properties };
    delete nextProperties[key];
    return Object.keys(nextProperties).length > 0 ? withProperties(container, nextProperties) : container;
  }
  const next = current.type === 'array' && isRecord(current.items)
    ? { ...current, items: removePropertyAtPath(current.items, rest) }
    : removePropertyAtPath(current, rest);
  return withProperties(container, { ...properties, [key]: next });
}

function withProperties(
  container: Record<string, unknown>,
  properties: Record<string, unknown>,
): Record<string, unknown> {
  return {
    ...container,
    type: 'object',
    additionalProperties: false,
    properties,
    required: Object.keys(properties),
  };
}

function toMultipleValues(current: Record<string, unknown>): Record<string, unknown> {
  const { title, description, ...items } = current;
  return {
    title,
    description,
    type: 'array',
    items: Object.keys(items).length > 0 ? items : { type: 'string' },
  };
}

function toSingleValue(current: Record<string, unknown>): Record<string, unknown> {
  if (current.type !== 'array' || !isRecord(current.items)) return current;
  const { title, description } = current;
  return {
    ...current.items,
    title: title ?? current.items.title,
    description: description ?? current.items.description,
  };
}

function childObjectSchema(schema: Record<string, unknown>): Record<string, unknown> | null {
  if (schema.type === 'object' && Object.keys(schemaProperties(schema)).length > 0) return schema;
  if (schema.type === 'array' && isRecord(schema.items) && schema.items.type === 'object') {
    return Object.keys(schemaProperties(schema.items)).length > 0 ? schema.items : null;
  }
  return null;
}

function schemaProperties(schema: Record<string, unknown>): Record<string, unknown> {
  return isRecord(schema.properties) ? schema.properties : {};
}

function nextResultKey(properties: Record<string, unknown>): string {
  let index = Object.keys(properties).length + 1;
  let key = `result_${index}`;
  while (key in properties) {
    index += 1;
    key = `result_${index}`;
  }
  return key;
}

function referencePathFor(schema: Record<string, unknown>, path: string[]): string {
  let container = schema;
  const parts: string[] = [];
  path.forEach((key, index) => {
    const current = schemaProperties(container)[key];
    if (!isRecord(current)) return;
    const hasChild = index < path.length - 1;
    parts.push(`${key}${hasChild && current.type === 'array' ? '[]' : ''}`);
    if (current.type === 'array' && isRecord(current.items)) container = current.items;
    else container = current;
  });
  return parts.join('.');
}

function findDependentNodeLabels(graph: Graph, sourceNodeId: string, reference: string): string[] {
  const targetIds = new Set(
    graph.edges.filter((edge) => edge.source === sourceNodeId).map((edge) => edge.target),
  );
  return graph.nodes
    .filter((node) => targetIds.has(node.id) && nodePrompt(node) && containsReference(nodePrompt(node), reference))
    .map((node) => node.title?.trim() || node.id);
}

function nodePrompt(node: WorkflowNode): string {
  return node.type === 'generate' || node.type === 'condition' || node.type === 'output' ? node.prompt : '';
}

function containsReference(prompt: string, reference: string): boolean {
  if (!reference) return false;
  const escaped = reference.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`(^|[^\\p{L}\\p{N}_-])${escaped}(?=$|[^\\p{L}\\p{N}_-])`, 'u').test(prompt);
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
