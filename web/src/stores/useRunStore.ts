import { create } from 'zustand';
import type { App, AgentChunk, ConditionBranchOverride, DecisionAnswer, DecisionGroup, DecisionRequestContext, Graph, Run, RunEvent, Step, WorkflowNode } from '../types';
import * as api from '../lib/api';
import { reduceRunDecisionState } from '../lib/runDecisionEvents';
import { openRunStream, type RunStream } from '../lib/ws';
import { blockingWorkflowLintMessage } from '../lib/workflowLint';
import { showCaughtError, showErrorDialog } from './useErrorDialogStore';

export type RunUiStatus = 'idle' | 'starting' | Run['status'];
type RunMode = 'live' | 'replay';

export function isCancellableRunStatus(status: RunUiStatus): boolean {
  return status === 'pending' || status === 'running' || status === 'waiting_for_user';
}

export function isRestorableRunStatus(status: Run['status']): boolean {
  return status === 'pending' || status === 'running' || status === 'waiting_for_user' || status === 'interrupted';
}

export interface WaitingInputRequest {
  node_id: string;
  context: DecisionRequestContext;
  groups: DecisionGroup[];
  /** 后端 decision_request.request_id；阶段 4 提交 resume 时回传。 */
  request_id: string;
}

interface RunStoreState {
  runId: string | null;
  appId: string | null;
  status: RunUiStatus;
  steps: Record<string, Step>;
  runGraph: Graph | null;
  deltas: Record<string, string>;
  error: string | null;
  mode: RunMode;
  replayRun: Run | null;
  waitingInput: WaitingInputRequest | null;

  start(app: App, inputs: Record<string, unknown>): Promise<string>;
  rerunFrom(
    sourceRunId: string,
    app: App,
    nodeId: string,
    inputs?: Record<string, unknown>,
    conditionBranchOverride?: ConditionBranchOverride,
  ): Promise<string>;
  restoreActiveRun(app: App): Promise<void>;
  resume(app: App, run: Run): void;
  continueRun(): Promise<void>;
  submitWaitingInput(payload: WaitingInputSubmit): Promise<void>;
  cancel(): Promise<void>;
  replay(run: Run): void;
  reset(): void;
}

/** WaitingInputPanel 提交时携带的 payload；store 内部转成 POST /api/runs/{id}/resume 请求体。 */
interface WaitingInputSubmit {
  answers?: DecisionAnswer[];
  text?: string;
  attachments?: { id: string; name?: string }[];
}

interface ActiveRunRef {
  appId: string;
  runId: string;
}

const ACTIVE_RUN_KEY = 'mira.activeRun.v1';

/** 当前打开的 SSE 流；切换 run / reset 时主动关闭。 */
let activeStream: RunStream | null = null;
/** 用于忽略已经被替换 / 取消的 run 事件回调。 */
let activeRunToken = 0;

function readActiveRun(): ActiveRunRef | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(ACTIVE_RUN_KEY);
    return raw ? (JSON.parse(raw) as ActiveRunRef) : null;
  } catch {
    return null;
  }
}

function clearActiveRun(): void {
  if (typeof window === 'undefined') return;
  window.sessionStorage.removeItem(ACTIVE_RUN_KEY);
}

function writeActiveRun(ref: ActiveRunRef): void {
  if (typeof window === 'undefined') return;
  window.sessionStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify(ref));
}

function nowIso(): string {
  return new Date().toISOString();
}

function emptyStep(node: WorkflowNode): Step {
  return {
    node_id: node.id,
    status: 'pending',
    input: null,
    output: null,
    logs: [],
  };
}

function closeStream(): void {
  if (activeStream) {
    try {
      activeStream.close();
    } catch {
      // ignore
    }
    activeStream = null;
  }
}

function appendDelta(prev: string | undefined, chunk: AgentChunk): string {
  if (chunk.type === 'text' && typeof chunk.text === 'string') {
    return (prev ?? '') + chunk.text;
  }
  return prev ?? '';
}

function stepsArrayToMap(steps: Step[]): Record<string, Step> {
  return Object.fromEntries(steps.map((step) => [step.node_id, step]));
}

function statusFromRun(run: Run): RunUiStatus {
  return run.status;
}

function waitingInputFromRun(run: Run): WaitingInputRequest | null {
  const req = run.recovery?.waiting_request;
  if (!req) return null;
  const nodeId = run.recovery?.resume_from_node_id ?? run.steps.find((step) => step.status === 'waiting_for_user')?.node_id;
  if (!nodeId) return null;
  return {
    node_id: nodeId,
    context: req.context,
    groups: req.groups ?? [],
    request_id: req.request_id,
  };
}

export const useRunStore = create<RunStoreState>((set, get) => ({
  runId: null,
  appId: null,
  status: 'idle',
  steps: {},
  runGraph: null,
  deltas: {},
  error: null,
  mode: 'live',
  replayRun: null,
  waitingInput: null,

  async restoreActiveRun(app) {
    if (get().runId !== null) return;
    const active = readActiveRun();
    if (!active || active.appId !== app.id) return;
    const token = activeRunToken;
    try {
      const run = await api.getRun(active.runId);
      if (token !== activeRunToken || get().runId !== null) return;
      if (run.app_id !== app.id) {
        clearActiveRun();
        return;
      }
      if (isRestorableRunStatus(run.status)) {
        get().resume(app, run);
      } else {
        clearActiveRun();
      }
    } catch {
      if (token === activeRunToken) clearActiveRun();
    }
  },

  async start(app, inputs) {
    closeStream();
    const token = ++activeRunToken;
    const steps = Object.fromEntries(app.graph.nodes.map((node) => [node.id, emptyStep(node)]));
    set({
      runId: null,
      appId: app.id,
      status: 'starting',
      steps,
      runGraph: app.graph,
      deltas: {},
      error: null,
      mode: 'live',
      replayRun: null,
      waitingInput: null,
    });

    let runId: string;
    let graph: Graph;
    try {
      if (!app.can_run) throw new Error('应用已下架，不能继续运行');
      const lint = await api.lintAppGraph(app.id, app.can_view_source ? app.graph : undefined);
      const lintMessage = blockingWorkflowLintMessage(lint);
      if (lintMessage) throw new Error(lintMessage);
      const created = await api.createRun({ app_id: app.id, inputs });
      runId = created.run_id;
      graph = created.graph;
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建运行失败';
      if (token === activeRunToken) {
        set({ status: 'idle', runGraph: null, error: message });
      }
      throw error;
    }
    if (token !== activeRunToken) return runId;

    writeActiveRun({ appId: app.id, runId });
    set({
      runId,
      status: 'pending',
      runGraph: graph,
      steps: Object.fromEntries(graph.nodes.map((node) => [node.id, emptyStep(node)])),
    });

    activeStream = openRunStream({
      runId,
      onEvent: (evt) => handleRunEvent(token, runId, evt, set, get),
      onRecover: async () => {
        if (token !== activeRunToken) return;
        try {
          const run = await api.getRun(runId);
          applyRunSnapshot(token, run, set);
        } catch {
          // 忽略：onRecover 失败时让 run.end 自然走完
        }
      },
    });
    return runId;
  },

  async rerunFrom(sourceRunId, app, nodeId, inputs, conditionBranchOverride) {
    closeStream();
    const token = ++activeRunToken;
    const steps = Object.fromEntries(app.graph.nodes.map((node) => [node.id, emptyStep(node)]));
    set({
      runId: null,
      appId: app.id,
      status: 'starting',
      steps,
      runGraph: app.graph,
      deltas: {},
      error: null,
      mode: 'live',
      replayRun: null,
      waitingInput: null,
    });

    let runId: string;
    let graph: Graph;
    try {
      const lint = await api.lintAppGraph(app.id, app.can_view_source ? app.graph : undefined);
      const lintMessage = blockingWorkflowLintMessage(lint);
      if (lintMessage) throw new Error(lintMessage);
      const created = await api.rerunFrom(sourceRunId, {
        app_id: app.id,
        node_id: nodeId,
        ...(inputs ? { inputs } : {}),
        ...(conditionBranchOverride ? { condition_branch_override: conditionBranchOverride } : {}),
      });
      runId = created.run_id;
      graph = created.graph;
    } catch (error) {
      const message = error instanceof Error ? error.message : '创建重新执行失败';
      if (token === activeRunToken) {
        set({ status: 'idle', runGraph: null, error: message });
      }
      throw error;
    }
    if (token !== activeRunToken) return runId;

    writeActiveRun({ appId: app.id, runId });
    set({
      runId,
      status: 'pending',
      runGraph: graph,
      steps: Object.fromEntries(graph.nodes.map((node) => [node.id, emptyStep(node)])),
    });
    try {
      const snapshot = await api.getRun(runId);
      applyRunSnapshot(token, snapshot, set);
    } catch {
      // SSE 仍会补齐后续事件；快照失败不阻断重新执行。
    }

    if (token !== activeRunToken) return runId;
    activeStream = openRunStream({
      runId,
      onEvent: (evt) => handleRunEvent(token, runId, evt, set, get),
      onRecover: async () => {
        if (token !== activeRunToken) return;
        try {
          const run = await api.getRun(runId);
          applyRunSnapshot(token, run, set);
        } catch {
          // ignore
        }
      },
    });
    return runId;
  },

  resume(_app, run) {
    closeStream();
    const token = ++activeRunToken;
    writeActiveRun({ appId: run.app_id, runId: run.id });
    set({
      runId: run.id,
      appId: run.app_id,
      status: statusFromRun(run),
      steps: stepsArrayToMap(run.steps),
      runGraph: run.graph,
      deltas: {},
      error: run.error ?? null,
      mode: 'live',
      replayRun: null,
      waitingInput: waitingInputFromRun(run),
    });
    if (run.status === 'success' || run.status === 'failed' || run.status === 'cancelled') {
      clearActiveRun();
      return;
    }
    if (run.status === 'interrupted') {
      return;
    }
    activeStream = openRunStream({
      runId: run.id,
      onEvent: (evt) => handleRunEvent(token, run.id, evt, set, get),
      onRecover: async () => {
        if (token !== activeRunToken) return;
        try {
          const latest = await api.getRun(run.id);
          applyRunSnapshot(token, latest, set);
        } catch {
          // ignore
        }
      },
    });
  },

  async continueRun() {
    const current = get();
    if (!current.runId || !current.appId) return;
    closeStream();
    const token = ++activeRunToken;
    writeActiveRun({ appId: current.appId ?? '', runId: current.runId });
    set({
      runId: current.runId,
      appId: current.appId,
      status: 'running',
      runGraph: current.runGraph,
      deltas: {},
      error: null,
      mode: 'live',
      replayRun: null,
      waitingInput: null,
    });
    let continued: Run;
    try {
      continued = await api.continueRun(current.runId);
    } catch (error) {
      if (token === activeRunToken) {
        set({ status: current.status, error: error instanceof Error ? error.message : '继续运行失败' });
      }
      showCaughtError(error, '继续运行失败', '继续运行失败');
      throw error;
    }
    if (token !== activeRunToken) return;
    writeActiveRun({ appId: continued.app_id, runId: continued.id });
    applyRunSnapshot(token, continued, set);
    activeStream = openRunStream({
      runId: continued.id,
      onEvent: (evt) => handleRunEvent(token, continued.id, evt, set, get),
      onRecover: async () => {
        if (token !== activeRunToken) return;
        try {
          const latest = await api.getRun(continued.id);
          applyRunSnapshot(token, latest, set);
        } catch {
          // ignore
        }
      },
    });
  },

  async submitWaitingInput(payload) {
    const state = get();
    const waitingInput = state.waitingInput;
    const runId = state.runId;
    if (!waitingInput || !runId) return;
    if (!waitingInput.request_id) {
      // 后端 spec §2.1 必带 request_id；缺失视为协议异常，让 UI 提示再次发起运行。
      set({ error: '当前决策请求已失效，请重新发起运行' });
      return;
    }
    // 提交后切回 running，等待后端继续推 step.start / delta；失败时恢复 waiting 面板。
    set({ waitingInput: null, status: 'running', error: null });
    try {
      await api.resumeRun(runId, {
        node_id: waitingInput.node_id,
        request_id: waitingInput.request_id,
        answers: payload.answers ?? [],
        text: payload.text ?? null,
        attachments: payload.attachments ?? [],
      });
      if (!activeStream && get().runId === runId) {
        const token = activeRunToken;
        activeStream = openRunStream({
          runId,
          onEvent: (evt) => handleRunEvent(token, runId, evt, set, get),
          onRecover: async () => {
            if (token !== activeRunToken) return;
            try {
              const latest = await api.getRun(runId);
              applyRunSnapshot(token, latest, set);
            } catch {
              // ignore
            }
          },
        });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '提交失败';
      // 提交失败：恢复 waitingInput，让用户能看到 panel 并重试。
      set({ waitingInput, status: 'waiting_for_user', error: message });
      showErrorDialog(message, '提交失败');
    }
  },

  async cancel() {
    const { runId } = get();
    if (!runId) return;
    const token = activeRunToken;
    set({ error: null });
    try {
      await api.cancelRun(runId);
    } catch {
      // 忽略：服务端可能已经处于终态；最终状态由 SSE / GET 补齐。
    }
    if (token !== activeRunToken || get().runId !== runId) return;
    try {
      const latest = await api.getRun(runId);
      if (token !== activeRunToken || get().runId !== runId) return;
      applyRunSnapshot(token, latest, set);
    } catch {
      // 忽略：SSE 仍可能补齐最终取消状态。
    }
  },

  replay(run) {
    closeStream();
    activeRunToken += 1;
    clearActiveRun();
    set({
      runId: run.id,
      appId: run.app_id,
      status: statusFromRun(run),
      steps: stepsArrayToMap(run.steps),
      runGraph: run.graph,
      deltas: {},
      error: run.error ?? null,
      mode: 'replay',
      replayRun: run,
      waitingInput: null,
    });
  },

  reset() {
    closeStream();
    activeRunToken += 1;
    clearActiveRun();
    set({
      runId: null,
      appId: null,
      status: 'idle',
      steps: {},
      runGraph: null,
      deltas: {},
      error: null,
      mode: 'live',
      replayRun: null,
      waitingInput: null,
    });
  },
}));

function handleRunEvent(
  token: number,
  runId: string,
  evt: RunEvent,
  set: (
    update: Partial<RunStoreState> | ((state: RunStoreState) => Partial<RunStoreState>),
  ) => void,
  get: () => RunStoreState,
): void {
  if (token !== activeRunToken) return;
  if (get().runId !== runId) return;
  switch (evt.event) {
    case 'step.start':
      set((state) => ({
        status: 'running',
        waitingInput: state.waitingInput?.node_id === evt.node_id ? null : state.waitingInput,
        steps: {
          ...state.steps,
          [evt.node_id]: {
            ...(state.steps[evt.node_id] ?? { node_id: evt.node_id, input: null, output: null, logs: [] }),
            status: 'running',
            started_at: evt.ts || nowIso(),
          },
        },
        deltas: { ...state.deltas, [evt.node_id]: '' },
      }));
      return;
    case 'step.log':
      set((state) => {
        const current = state.steps[evt.node_id] ?? {
          node_id: evt.node_id,
          status: 'running',
          input: null,
          output: null,
          logs: [],
        };
        return {
          steps: {
            ...state.steps,
            [evt.node_id]: {
              ...current,
              logs: [...(current.logs ?? []), evt.log],
            },
          },
        };
      });
      return;
    case 'step.delta':
      set((state) => ({
        deltas: {
          ...state.deltas,
          [evt.node_id]: appendDelta(state.deltas[evt.node_id], evt.chunk),
        },
      }));
      return;
    case 'step.end': {
      const incoming = evt.step;
      set((state) => ({
        steps: {
          ...state.steps,
          [evt.node_id]: {
            ...(state.steps[evt.node_id] ?? { node_id: evt.node_id, input: null, output: null, logs: [] }),
            ...incoming,
          },
        },
      }));
      return;
    }
    case 'step.waiting':
    case 'run.waiting_for_user':
    case 'run.resumed':
      set((state) => reduceRunDecisionState(state, evt));
      return;
    case 'run.end':
      closeStream();
      clearActiveRun();
      if (evt.status === 'failed' && evt.error) {
        showErrorDialog(evt.error, '运行失败');
      }
      set({
        status: evt.status,
        error: evt.error ?? null,
        waitingInput: null,
      });
      return;
    default:
      return;
  }
}

function applyRunSnapshot(
  token: number,
  run: Run,
  set: (update: Partial<RunStoreState>) => void,
): void {
  if (token !== activeRunToken) return;
  const terminal = run.status === 'success' || run.status === 'failed' || run.status === 'cancelled';
  set({
    runId: run.id,
    appId: run.app_id,
    status: statusFromRun(run),
    steps: stepsArrayToMap(run.steps),
    runGraph: run.graph,
    error: run.error ?? null,
    waitingInput: terminal ? null : waitingInputFromRun(run),
  });
  if (terminal) {
    closeStream();
    clearActiveRun();
  }
}
