// Editor state for the canvas. We keep the canonical graph here and React Flow
// derives its visual nodes/edges from it. Auto-save (300ms debounce) persists
// via PATCH /api/apps/:id (PRD §8.3).

import { create } from 'zustand';
import type { App, AppAgentKind, Graph, GraphNodeSizeMap, RunWaitingRequest, ExecutionEdge, WorkflowNode, NodeType } from '../types';
import { CONDITION_DEFAULT_BRANCH_KEY } from '../types';
import * as api from '../lib/api';
import { defaultReasoningEffortForAgent, normalizeReasoningEffortForAgent } from '../lib/agentOptions';
import { uuid } from '../lib/utils';

type SaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'error';
type GraphUpdateOptions = { skipHistory?: boolean; skipSave?: boolean };

interface History {
  past: Graph[];
  future: Graph[];
}

export interface PromptAssistantGenerationState {
  appId: string;
  nodeId: string;
  generationId: string;
  controller: AbortController;
  request: string;
  waitingRequest?: RunWaitingRequest;
}

interface EditorStoreState {
  app: App | null;
  selectedId: string | null;
  selectedIds: string[];
  foregroundNodeId: string | null;
  focusRequest: { id: string; seq: number } | null;
  saveStatus: SaveStatus;
  saveError: string | null;
  history: History;
  promptAssistantGenerations: Record<string, PromptAssistantGenerationState>;

  load(id: string): Promise<void>;
  rename(name: string): void;
  setMeta(patch: { name?: string; description?: string; cover?: string | null }): void;
  flushSave(): Promise<void>;
  publish(payload?: { visibility?: App['visibility']; market_access?: App['market_access'] }): Promise<void>;
  unpublish(): Promise<void>;
  setGraphAgent(agent: AppAgentKind, supportedModels?: string[]): void;
  setGraph(graph: Graph, opts?: GraphUpdateOptions): void;
  patchNode(id: string, patch: Partial<WorkflowNode>, opts?: GraphUpdateOptions): void;
  addNode(type: NodeType, init?: Partial<WorkflowNode>): WorkflowNode;
  beautifyLayout(nodeSizes?: GraphNodeSizeMap): Promise<void>;
  removeNode(id: string): void;
  addEdge(edge: ExecutionEdge): void;
  removeEdge(id: string): void;
  setSelected(id: string | null): void;
  setSelectedIds(ids: string[]): void;
  focusCanvasNode(id: string): void;

  undo(): void;
  redo(): void;

  startPromptAssistantGeneration(generation: PromptAssistantGenerationState): void;
  finishPromptAssistantGeneration(nodeId: string, generationId: string): void;
}

// Debounced save. One timer per store instance, fine since editor is singleton.
let saveTimer: number | undefined;
let saveVersion = 0;
let editVersion = 0;
let loadVersion = 0;

async function performSave(
  get: () => EditorStoreState,
  set: (p: Partial<EditorStoreState>) => void,
  opts: { throwOnError?: boolean } = {},
) {
  const version = ++saveVersion;
  const requestedEditVersion = editVersion;
  const app = get().app;
  if (!app) return;
  const appId = app.id;
  set({ saveStatus: 'saving', saveError: null });
  try {
    const updated = await api.patchApp(app.id, {
      name: app.name,
      description: app.description,
      cover: app.cover,
      graph: app.graph,
    });
    const current = get().app;
    if (version !== saveVersion || !current || current.id !== appId) return;
    if (editVersion !== requestedEditVersion) return;
    // refresh in-memory app (timestamps moved) without re-resetting history
    set({ app: updated, saveStatus: 'saved', saveError: null });
  } catch (error) {
    if (version !== saveVersion) return;
    if (editVersion !== requestedEditVersion) return;
    const message = error instanceof Error ? error.message : '保存失败';
    set({ saveStatus: 'error', saveError: message });
    if (opts.throwOnError) throw error;
  }
}

function scheduleSave(get: () => EditorStoreState, set: (p: Partial<EditorStoreState>) => void) {
  if (saveTimer) window.clearTimeout(saveTimer);
  set({ saveStatus: 'saving', saveError: null });
  saveTimer = window.setTimeout(() => {
    saveTimer = undefined;
    void performSave(get, set);
  }, 300);
}

// Flush any pending debounced save immediately. Awaits the network round-trip
// so callers (e.g., publish) can rely on persisted state being up-to-date.
async function flushPendingSave(
  get: () => EditorStoreState,
  set: (p: Partial<EditorStoreState>) => void,
) {
  if (!saveTimer) {
    if (get().saveStatus === 'dirty') {
      await performSave(get, set, { throwOnError: true });
      return;
    }
    if (get().saveStatus === 'error') throw new Error(get().saveError ?? '保存失败');
    return;
  }
  window.clearTimeout(saveTimer);
  saveTimer = undefined;
  await performSave(get, set, { throwOnError: true });
}

function pushHistory(history: History, graph: Graph): History {
  // Cap history to ~30 to keep memory bounded
  const past = [...history.past, graph].slice(-30);
  return { past, future: [] };
}

function sameIds(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((id, index) => id === b[index]);
}

function effectiveConditionKeys(node: WorkflowNode): Set<string> {
  if (node.type !== 'condition') return new Set();
  if (node.mode === 'binary') return new Set(['true', 'false']);
  const keys = new Set<string>();
  for (const branch of node.branches ?? []) {
    if (branch && typeof branch.key === 'string' && branch.key) keys.add(branch.key);
  }
  keys.add(CONDITION_DEFAULT_BRANCH_KEY);
  return keys;
}

// 当 condition 节点的 mode/branches 变化后，孤儿 edges（branch_key 不在新 keys 集合里）应当被删除。
function pruneOrphanConditionEdges(nodes: WorkflowNode[], edges: ExecutionEdge[]): ExecutionEdge[] {
  const conditionKeysById = new Map<string, Set<string>>();
  for (const node of nodes) {
    if (node.type === 'condition') conditionKeysById.set(node.id, effectiveConditionKeys(node));
  }
  if (conditionKeysById.size === 0) return edges;
  return edges.filter((edge) => {
    const valid = conditionKeysById.get(edge.source);
    if (!valid) return true; // 非 condition 起点，保留
    const handle = edge.branch_key ?? '';
    return valid.has(handle);
  });
}

function clearIncompatibleModels(nodes: WorkflowNode[], supportedModels: string[] | undefined): WorkflowNode[] {
  const allowed = new Set(supportedModels ?? []);
  return nodes.map((node) => {
    if (
      (node.type === 'generate' || node.type === 'output' || node.type === 'condition') &&
      node.model &&
      !allowed.has(node.model)
    ) {
      return { ...node, model: undefined } as WorkflowNode;
    }
    return node;
  });
}

function normalizeNodeReasoningEfforts(nodes: WorkflowNode[], agent: AppAgentKind): WorkflowNode[] {
  return nodes.map((node) => {
    if (node.type !== 'generate' && node.type !== 'output' && node.type !== 'condition') return node;
    const reasoning_effort = normalizeReasoningEffortForAgent(agent, node.reasoning_effort);
    if (reasoning_effort === node.reasoning_effort) return node;
    return { ...node, reasoning_effort } as WorkflowNode;
  });
}

function isSingletonNodeType(type: NodeType): boolean {
  return type === 'user_input' || type === 'output';
}

export const useEditorStore = create<EditorStoreState>((set, get) => ({
  app: null,
  selectedId: null,
  selectedIds: [],
  foregroundNodeId: null,
  focusRequest: null,
  saveStatus: 'idle',
  saveError: null,
  history: { past: [], future: [] },
  promptAssistantGenerations: {},

  async load(id) {
    const version = ++loadVersion;
    saveVersion += 1;
    editVersion = 0;
    if (saveTimer) {
      window.clearTimeout(saveTimer);
      saveTimer = undefined;
    }
    const app = await api.getApp(id);
    if (version !== loadVersion) return;
    set({
      app,
      selectedId: null,
      selectedIds: [],
      foregroundNodeId: null,
      focusRequest: null,
      history: { past: [], future: [] },
      saveStatus: 'saved',
      saveError: null,
      promptAssistantGenerations: {},
    });
  },

  rename(name) {
    const app = get().app;
    if (!app) return;
    editVersion += 1;
    set({ app: { ...app, name } });
    scheduleSave(get, set);
  },

  setMeta(patch) {
    const app = get().app;
    if (!app) return;
    const next: typeof app = { ...app };
    if (typeof patch.name === 'string') next.name = patch.name;
    if (typeof patch.description === 'string') next.description = patch.description;
    if (patch.cover !== undefined) next.cover = patch.cover;
    if (
      next.name === app.name &&
      next.description === app.description &&
      next.cover === app.cover
    ) {
      return;
    }
    editVersion += 1;
    set({ app: next });
    scheduleSave(get, set);
  },

  async flushSave() {
    await flushPendingSave(get, set);
  },

  async publish(payload) {
    await flushPendingSave(get, set);
    const app = get().app;
    if (!app) return;
    const appId = app.id;
    const { app: updated } = await api.publishApp(appId, payload);
    const current = get().app;
    if (!current || current.id !== appId) return;
    set({ app: updated, saveStatus: 'saved', saveError: null });
  },

  async unpublish() {
    const app = get().app;
    if (!app) return;
    const appId = app.id;
    const updated = await api.unpublishApp(appId);
    const current = get().app;
    if (!current || current.id !== appId) return;
    set({ app: updated, saveStatus: 'saved', saveError: null });
  },

  setGraphAgent(agent, supportedModels) {
    const app = get().app;
    if (!app) return;
    const nodes = normalizeNodeReasoningEfforts(
      clearIncompatibleModels(app.graph.nodes, supportedModels),
      agent,
    );
    get().setGraph({
      ...app.graph,
      agent,
      nodes,
    });
  },

  setGraph(graph, opts) {
    const app = get().app;
    if (!app) return;
    const history = get().history;
    const newHistory = opts?.skipHistory ? { past: history.past, future: [] } : pushHistory(history, app.graph);
    const nodeIds = new Set(graph.nodes.map((n) => n.id));
    const selectedIds = get().selectedIds.filter((id) => nodeIds.has(id));
    const currentForegroundNodeId = get().foregroundNodeId;
    const foregroundNodeId = currentForegroundNodeId && nodeIds.has(currentForegroundNodeId) ? currentForegroundNodeId : null;
    editVersion += 1;
    if (opts?.skipSave && saveTimer) {
      window.clearTimeout(saveTimer);
      saveTimer = undefined;
    }
    set({
      app: { ...app, graph },
      history: newHistory,
      selectedIds,
      selectedId: selectedIds.length === 1 ? selectedIds[0] : null,
      foregroundNodeId,
      ...(opts?.skipSave ? { saveStatus: 'dirty' as SaveStatus, saveError: null } : {}),
    });
    if (!opts?.skipSave) scheduleSave(get, set);
  },

  patchNode(id, patch, opts) {
    const app = get().app;
    if (!app) return;
    const nodes = app.graph.nodes.map((n) =>
      n.id === id ? ({ ...n, ...patch } as WorkflowNode) : n,
    );
    // condition 节点的 mode / branches 改动后，可能让某些 edge 的 branch_key 失效，需要剪枝。
    const target = nodes.find((n) => n.id === id);
    const touchesConditionShape =
      target?.type === 'condition' && ('mode' in patch || 'branches' in patch);
    const edges = touchesConditionShape
      ? pruneOrphanConditionEdges(nodes, app.graph.execution_edges)
      : app.graph.execution_edges;
    get().setGraph({ ...app.graph, nodes, execution_edges: edges }, opts);
  },

  addNode(type, init) {
    const app = get().app;
    if (!app) throw new Error('no app');
    if (isSingletonNodeType(type)) {
      const existing = app.graph.nodes.find((node) => node.type === type);
      if (existing) {
        const current = get().focusRequest?.seq ?? 0;
        set({
          selectedId: existing.id,
          selectedIds: [existing.id],
          foregroundNodeId: existing.id,
          focusRequest: { id: existing.id, seq: current + 1 },
        });
        return existing;
      }
    }
    const id = 'n_' + uuid();
    const baseX = 120 + app.graph.nodes.length * 80;
    const baseY = 200 + (app.graph.nodes.length % 3) * 40;
    let node: WorkflowNode;
    switch (type) {
      case 'user_input':
        node = {
          id,
          type: 'user_input',
          position: { x: baseX, y: baseY },
          title: '用户输入',
          input_schema: { label: '你的输入', kind: 'text', required: true },
        };
        break;
      case 'generate':
        node = {
          id,
          type: 'generate',
          position: { x: baseX, y: baseY },
          title: '生成',
          prompt: '',
          reasoning_effort: defaultReasoningEffortForAgent(app.graph.agent),
        };
        break;
      case 'output':
        node = {
          id,
          type: 'output',
          position: { x: baseX, y: baseY },
          title: '输出',
          prompt: '',
          reasoning_effort: defaultReasoningEffortForAgent(app.graph.agent),
        };
        break;
      case 'condition':
        node = {
          id,
          type: 'condition',
          position: { x: baseX, y: baseY },
          title: '判断',
          mode: 'binary',
          prompt: '',
          reasoning_effort: defaultReasoningEffortForAgent(app.graph.agent),
          branches: [{ key: 'true' }, { key: 'false' }],
        };
        break;
      case 'asset':
      default:
        node = {
          id,
          type: 'asset',
          position: { x: baseX, y: baseY },
          title: '素材',
          asset_kind: 'text',
          content: '',
        };
        break;
    }
    if (init) node = { ...node, ...init } as WorkflowNode;
    get().setGraph({ ...app.graph, nodes: [...app.graph.nodes, node], execution_edges: app.graph.execution_edges });
    set({ selectedId: id, selectedIds: [id], foregroundNodeId: id });
    return node;
  },

  async beautifyLayout(nodeSizes) {
    const app = get().app;
    if (!app) return;
    const { graph } = await api.beautifyGraphLayout({
      app_id: app.id,
      graph: app.graph,
      node_sizes: nodeSizes ?? {},
    });
    const current = get().app;
    if (!current || current.id !== app.id) return;
    get().setGraph(graph);
  },

  removeNode(id) {
    const app = get().app;
    if (!app) return;
    const remainingEdges = app.graph.execution_edges.filter((e) => e.source !== id && e.target !== id);
    const nodes = app.graph.nodes.filter((n) => n.id !== id);
    get().setGraph({ ...app.graph, nodes, execution_edges: remainingEdges });
    if (get().selectedIds.includes(id)) {
      const selectedIds = get().selectedIds.filter((selected) => selected !== id);
      set({ selectedIds, selectedId: selectedIds.length === 1 ? selectedIds[0] : null });
    }
    if (get().foregroundNodeId === id) set({ foregroundNodeId: null });
  },

  addEdge(edge) {
    const app = get().app;
    if (!app) return;
    if (edge.source === edge.target) return;
    const sourceNode = app.graph.nodes.find((n) => n.id === edge.source);
    const targetNode = app.graph.nodes.find((n) => n.id === edge.target);
    if (!sourceNode || !targetNode) return;
    if (sourceNode.type === 'output') return;
    if (targetNode.type === 'user_input' || targetNode.type === 'asset') return;
    if (sourceNode.type === 'condition' && (!edge.branch_key || !effectiveConditionKeys(sourceNode).has(edge.branch_key))) {
      return;
    }
    // Skip duplicates and two-node loops. Condition edges are keyed by branch handle.
    const duplicateOrLoop = app.graph.execution_edges.some((e) => {
      if (e.source === edge.target && e.target === edge.source) return true;
      if (e.source !== edge.source || e.target !== edge.target) return false;
      if (sourceNode?.type === 'condition') return (e.branch_key ?? null) === (edge.branch_key ?? null);
      return true;
    });
    if (duplicateOrLoop) {
      return;
    }
    if (
      sourceNode?.type === 'condition' &&
      app.graph.execution_edges.some((e) => e.source === edge.source && (e.branch_key ?? null) === (edge.branch_key ?? null))
    ) {
      return;
    }
    const normalizedEdge: ExecutionEdge = sourceNode.type === 'condition'
      ? edge
      : { ...edge, branch_key: undefined };
    const edges = [...app.graph.execution_edges, normalizedEdge];
    get().setGraph({
      ...app.graph,
      execution_edges: edges,
    });
  },

  removeEdge(id) {
    const app = get().app;
    if (!app) return;
    const edges = app.graph.execution_edges.filter((e) => e.id !== id);
    get().setGraph({
      ...app.graph,
      execution_edges: edges,
    });
  },

  setSelected(id) {
    if (get().selectedId === id && sameIds(get().selectedIds, id ? [id] : [])) return;
    set({ selectedId: id, selectedIds: id ? [id] : [] });
  },

  setSelectedIds(ids) {
    if (sameIds(get().selectedIds, ids)) return;
    set({ selectedIds: ids, selectedId: ids.length === 1 ? ids[0] : null });
  },

  focusCanvasNode(id) {
    const current = get().focusRequest?.seq ?? 0;
    set({ foregroundNodeId: id, focusRequest: { id, seq: current + 1 } });
  },

  undo() {
    const { app, history } = get();
    if (!app || history.past.length === 0) return;
    const previous = history.past[history.past.length - 1];
    const past = history.past.slice(0, -1);
    const future = [app.graph, ...history.future].slice(0, 30);
    editVersion += 1;
    set({ app: { ...app, graph: previous }, history: { past, future } });
    scheduleSave(get, set);
  },

  redo() {
    const { app, history } = get();
    if (!app || history.future.length === 0) return;
    const next = history.future[0];
    const future = history.future.slice(1);
    const past = [...history.past, app.graph].slice(-30);
    editVersion += 1;
    set({ app: { ...app, graph: next }, history: { past, future } });
    scheduleSave(get, set);
  },

  startPromptAssistantGeneration(generation) {
    set((state) => ({
      promptAssistantGenerations: {
        ...state.promptAssistantGenerations,
        [generation.nodeId]: generation,
      },
    }));
  },

  finishPromptAssistantGeneration(nodeId, generationId) {
    set((state) => {
      const current = state.promptAssistantGenerations[nodeId];
      if (!current || current.generationId !== generationId) return {};
      const next = { ...state.promptAssistantGenerations };
      delete next[nodeId];
      return { promptAssistantGenerations: next };
    });
  },
}));
