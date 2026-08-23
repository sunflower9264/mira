// Console tab — run timeline and per-node debug actions.

import { Search } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useEditorStore } from '../../stores/useEditorStore';
import { useRunStore, type RunUiStatus } from '../../stores/useRunStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { getRunStepTrace } from '../../lib/api';
import type {
  ConditionBranch,
  ConditionBranchOverride,
  ConditionNode,
  ConditionResult,
  GenerateNode,
  Graph,
  LogLine,
  OutputNode,
  RunStepTrace,
  Step,
  ExecutionEdge,
  WorkflowNode,
} from '../../types';
import { CONDITION_DEFAULT_BRANCH_KEY } from '../../types';
import { AppDialog } from '../common/AppDialog';

type LlmNode = GenerateNode | ConditionNode | OutputNode;

const EMPTY_NODES: WorkflowNode[] = [];

const STATUS_TONE: Record<RunUiStatus, string> = {
  idle: 'border-black/10 bg-white text-black/45',
  starting: 'border-blue-200 bg-blue-50 text-blue-700',
  pending: 'border-black/10 bg-black/[0.04] text-black/55',
  running: 'border-blue-200 bg-blue-50 text-blue-700',
  waiting_for_user: 'border-amber-200 bg-amber-50 text-amber-700',
  interrupted: 'border-amber-200 bg-amber-50 text-amber-700',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  failed: 'border-red-200 bg-red-50 text-red-700',
  cancelled: 'border-black/10 bg-black/[0.04] text-black/55',
};

const STATUS_LABEL: Record<RunUiStatus, string> = {
  idle: '待运行',
  starting: '启动中',
  pending: '等待中',
  running: '运行中',
  waiting_for_user: '等待用户',
  interrupted: '已中断',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
};

const STEP_LABEL: Record<Step['status'], string> = {
  pending: '等待中',
  running: '运行中',
  waiting_for_user: '等待用户',
  interrupted: '已中断',
  success: '成功',
  checkpoint_reused: '检查点复用',
  failed: '失败',
  skipped: '已跳过',
  cancelled: '已取消',
};

function runSummaryLabel(status: RunUiStatus, done: number, total: number): string {
  switch (status) {
    case 'success':
      return `已完成 ${done}/${total}`;
    case 'failed':
      return '运行失败';
    case 'interrupted':
      return '已中断';
    case 'cancelled':
      return '已取消';
    default:
      return `进度 ${done}/${total}`;
  }
}

function orderNodesByGraph(nodes: WorkflowNode[], edges: ExecutionEdge[]): WorkflowNode[] {
  if (nodes.length <= 1) return nodes;

  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const outgoing = new Map<string, string[]>();
  const incomingCount = new Map<string, number>();

  nodes.forEach((node) => {
    outgoing.set(node.id, []);
    incomingCount.set(node.id, 0);
  });

  edges.forEach((edge) => {
    if (!indexById.has(edge.source) || !indexById.has(edge.target)) return;
    outgoing.get(edge.source)?.push(edge.target);
    incomingCount.set(edge.target, (incomingCount.get(edge.target) ?? 0) + 1);
  });

  const compareNodes = (a: WorkflowNode, b: WorkflowNode) => {
    const hasPosition =
      Number.isFinite(a.position?.x) &&
      Number.isFinite(a.position?.y) &&
      Number.isFinite(b.position?.x) &&
      Number.isFinite(b.position?.y);
    if (hasPosition) {
      if (a.position.x !== b.position.x) return a.position.x - b.position.x;
      if (a.position.y !== b.position.y) return a.position.y - b.position.y;
    }

    return (indexById.get(a.id) ?? 0) - (indexById.get(b.id) ?? 0);
  };

  const ready = nodes
    .filter((node) => (incomingCount.get(node.id) ?? 0) === 0)
    .sort(compareNodes);
  const ordered: WorkflowNode[] = [];
  const seen = new Set<string>();

  while (ready.length) {
    const node = ready.shift();
    if (!node || seen.has(node.id)) continue;

    ordered.push(node);
    seen.add(node.id);

    (outgoing.get(node.id) ?? []).forEach((targetId) => {
      const nextCount = (incomingCount.get(targetId) ?? 0) - 1;
      incomingCount.set(targetId, nextCount);
      if (nextCount === 0) {
        const target = nodes[indexById.get(targetId) ?? -1];
        if (target) ready.push(target);
      }
    });
    ready.sort(compareNodes);
  }

  if (ordered.length === nodes.length) return ordered;

  const remaining = nodes.filter((node) => !seen.has(node.id)).sort(compareNodes);
  return ordered.concat(remaining);
}

export function ConsoleTab() {
  const app = useEditorStore((s) => s.app);
  const runGraph = useRunStore((s) => s.runGraph);
  const graph = runGraph ?? app?.graph;
  const nodes = graph?.nodes ?? EMPTY_NODES;
  const runId = useRunStore((s) => s.runId);
  const rerunFrom = useRunStore((s) => s.rerunFrom);
  const steps = useRunStore((s) => s.steps);
  const deltas = useRunStore((s) => s.deltas);
  const status = useRunStore((s) => s.status);
  const flushSave = useEditorStore((s) => s.flushSave);

  const [branchTesting, setBranchTesting] = useState<string | null>(null);
  const [branchTestError, setBranchTestError] = useState<string | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [trace, setTrace] = useState<RunStepTrace | null>(null);

  const orderedNodes = useMemo(
    () => orderNodesByGraph(nodes, graph?.execution_edges ?? []),
    [nodes, graph?.execution_edges],
  );

  const stats = useMemo(() => {
    let done = 0;
    let failed = 0;
    let totalMs = 0;
    nodes.forEach((n) => {
      const step = steps[n.id];
      if (!step) return;
      if (step.status === 'success' || step.status === 'failed' || step.status === 'cancelled') done += 1;
      if (step.status === 'failed') failed += 1;
      if (step.duration_ms) totalMs += step.duration_ms;
    });
    return { done, failed, totalMs };
  }, [nodes, steps]);

  if (!nodes.length) {
    return <div className="p-6 text-sm text-black/45">还没有步骤。请先向工作流添加节点。</div>;
  }

  const openTrace = (node: LlmNode) => {
    if (!runId) return;
    setTraceOpen(true);
    setTrace(null);
    setTraceError(null);
    setTraceLoading(true);
    void getRunStepTrace(runId, node.id)
      .then((payload) => setTrace(payload))
      .catch((error) => {
        showCaughtError(error, 'Trace 加载失败', '加载失败');
      })
      .finally(() => setTraceLoading(false));
  };

  const canStartBranchTest =
    !!app &&
    !!runId &&
    (status === 'success' || status === 'failed' || status === 'cancelled' || status === 'interrupted');

  const testConditionBranch = (node: ConditionNode, branchKey: string) => {
    if (!app || !runId) return;
    const override: ConditionBranchOverride = { node_id: node.id, branch_key: branchKey };
    setBranchTesting(`${node.id}:${branchKey}`);
    setBranchTestError(null);
    void flushSave()
      .then(() => rerunFrom(runId, app, node.id, undefined, override))
      .catch((error) => {
        showCaughtError(error, '分支测试启动失败', '启动失败');
      })
      .finally(() => setBranchTesting(null));
  };

  return (
    <div className="px-4 py-4 space-y-4">
      <header className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className={`rounded-full border px-2 py-0.5 ${STATUS_TONE[status]}`}>
          {STATUS_LABEL[status]}
        </span>
        <span className="rounded-full border border-black/10 bg-white px-2 py-0.5 text-black/65">
          {runSummaryLabel(status, stats.done, nodes.length)}
        </span>
        <span
          className={`rounded-full border px-2 py-0.5 ${
            stats.failed > 0
              ? 'border-red-200 bg-red-50 text-red-700'
              : 'border-black/10 bg-white text-black/55'
          }`}
        >
          错误 {stats.failed}
        </span>
        <span className="rounded-full border border-black/10 bg-white px-2 py-0.5 font-mono text-black/65">
          {formatDuration(stats.totalMs)}
        </span>
      </header>

      <TimelineView
        nodes={orderedNodes}
        steps={steps}
        deltas={deltas}
        runId={runId}
        onOpenTrace={openTrace}
        currentGraph={app?.graph ?? null}
        canStartBranchTest={canStartBranchTest}
        branchTesting={branchTesting}
        branchTestError={branchTestError}
        onTestConditionBranch={testConditionBranch}
      />
      <RunTraceDialog
        open={traceOpen}
        loading={traceLoading}
        error={traceError}
        trace={trace}
        onClose={() => setTraceOpen(false)}
      />
    </div>
  );
}

function nodeLabel(n: WorkflowNode | undefined) {
  if (!n) return '';
  return n.title || n.id;
}

function formatTime(iso: string) {
  if (!iso) return '';
  return iso.length >= 19 ? iso.slice(11, 19) : iso;
}

function formatDuration(ms: number) {
  if (!ms) return '0 ms';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function levelColor(level: string) {
  switch (level) {
    case 'error':
      return 'text-red-600';
    case 'warn':
      return 'text-amber-600';
    case 'tool':
      return 'text-indigo-600';
    default:
      return 'text-black/70';
  }
}

function levelChipTone(level: LogLine['level']) {
  switch (level) {
    case 'error':
      return 'border-red-200 bg-red-50 text-red-700';
    case 'warn':
      return 'border-amber-200 bg-amber-50 text-amber-700';
    case 'tool':
      return 'border-indigo-200 bg-indigo-50 text-indigo-700';
    default:
      return 'border-black/10 bg-white text-black/60';
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function compactText(value: string): string {
  return value.trim().replace(/\s+/g, ' ');
}

function looksLikeAttachment(value: Record<string, unknown>): value is Record<string, unknown> & { name: string } {
  return (
    typeof value.name === 'string' &&
    ('path' in value || 'mime' in value || 'size' in value)
  );
}

function sanitizeOutputValue(value: unknown, key?: string): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (key === 'attachments' && isRecord(item) && typeof item.name === 'string') {
        return compactText(item.name) || '未命名附件';
      }
      return sanitizeOutputValue(item);
    });
  }
  if (!isRecord(value)) return value;
  if (looksLikeAttachment(value)) return compactText(value.name) || '未命名附件';

  const next: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    next[key] = sanitizeOutputValue(item, key);
  }
  return next;
}

function formatOutputValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return '';
  try {
    return JSON.stringify(sanitizeOutputValue(value), null, 2);
  } catch {
    return String(value);
  }
}

function hasOutputValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  return true;
}

function previewOutputText(text: string, limit = 160): string {
  const compact = compactText(text);
  return compact.length > limit ? compact.slice(0, limit) + '…' : compact;
}

function stepOutputTs(step: Step | undefined): string {
  if (!step) return '';
  if (step.finished_at) return step.finished_at;
  if (step.started_at) return step.started_at;
  const logs = step.logs ?? [];
  return logs.length ? logs[logs.length - 1].ts : '';
}

function ChevronIcon({ open, className = '' }: { open: boolean; className?: string }) {
  return (
    <svg
      className={`size-3 shrink-0 transition ${open ? 'rotate-180' : ''} ${className}`}
      viewBox="0 0 12 12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 4.5l3 3 3-3" />
    </svg>
  );
}

function LogSection({
  title,
  children,
  tone,
}: {
  title: string;
  children: ReactNode;
  tone?: 'error';
}) {
  return (
    <section>
      <header
        className={`mb-1 text-[10px] uppercase tracking-wide ${
          tone === 'error' ? 'text-red-500' : 'text-black/45'
        }`}
      >
        {title}
      </header>
      {children}
    </section>
  );
}

function ConditionResultSummary({ result }: { result: ConditionResult }) {
  return (
    <LogSection title="分支结果">
      <div className="space-y-2 rounded-lg border border-black/10 bg-black/[0.02] px-3 py-2 text-[12px]">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-black/45">命中</span>
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 font-mono text-[10px] text-emerald-700">
            {branchLabel(result.chosen_branch)}
          </span>
          {result.forced ? (
            <span className="rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-[10px] text-blue-700">
              测试指定
            </span>
          ) : null}
        </div>
        {result.unchosen_branches.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-black/45">未走</span>
            {result.unchosen_branches.map((branch) => (
              <span key={branch} className="rounded-full border border-black/10 bg-white px-2 py-0.5 font-mono text-[10px] text-black/45">
                {branchLabel(branch)}
              </span>
            ))}
          </div>
        ) : null}
        <div className="text-black/60">{result.reason}</div>
      </div>
    </LogSection>
  );
}

function ConditionBranchTestPanel({
  node,
  currentGraph,
  canStart,
  testingKey,
  error,
  onTestBranch,
}: {
  node: ConditionNode;
  currentGraph: Graph | null;
  canStart: boolean;
  testingKey: string | null;
  error: string | null;
  onTestBranch(node: ConditionNode, branchKey: string): void;
}) {
  const currentNode = currentGraph?.nodes.find(
    (candidate): candidate is ConditionNode => candidate.id === node.id && candidate.type === 'condition',
  ) ?? null;
  const options = currentNode && currentGraph
    ? conditionBranchOptions(currentNode, currentGraph.execution_edges)
    : [];
  return (
    <LogSection title="分支测试">
      <div className="rounded-lg border border-black/10 bg-white px-3 py-2">
        {!currentNode ? (
          <div className="text-[12px] text-red-600">当前 App graph 中找不到该条件节点。</div>
        ) : options.length === 0 ? (
          <div className="text-[12px] text-black/45">当前 condition 没有可测试分支。</div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {options.map((option) => {
              const key = `${node.id}:${option.key}`;
              const busy = testingKey === key;
              return (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => onTestBranch(currentNode, option.key)}
                  disabled={!canStart || !option.connected || testingKey !== null}
                  title={option.connected ? '创建该分支的测试运行' : '该分支未连接下游'}
                  className="rounded-full border border-black/10 bg-black/[0.03] px-2.5 py-1 text-[11px] font-medium text-black/65 hover:border-black/20 hover:bg-black/[0.06] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busy ? '启动中' : option.label}
                </button>
              );
            })}
            {!canStart ? (
              <span className="text-[11px] text-black/40">运行结束后可测试分支。</span>
            ) : null}
          </div>
        )}
        {error ? <div className="mt-2 text-[12px] text-red-600">{error}</div> : null}
      </div>
    </LogSection>
  );
}

interface ConditionBranchOption {
  key: string;
  label: string;
  connected: boolean;
}

function conditionBranchOptions(node: ConditionNode, edges: ExecutionEdge[]): ConditionBranchOption[] {
  const branches: ConditionBranch[] = node.mode === 'binary'
    ? [{ key: 'true' }, { key: 'false' }]
    : node.branches;
  const options = branches.map((branch) => ({
    key: branch.key,
    label: branch.label?.trim() || branchLabel(branch.key),
    connected: conditionBranchConnected(edges, node.id, branch.key),
  }));
  if (node.mode === 'cases' && conditionBranchConnected(edges, node.id, CONDITION_DEFAULT_BRANCH_KEY)) {
    options.push({
      key: CONDITION_DEFAULT_BRANCH_KEY,
      label: '其它',
      connected: true,
    });
  }
  return options;
}

function conditionBranchConnected(edges: ExecutionEdge[], nodeId: string, branchKey: string): boolean {
  return edges.some((edge) => edge.source === nodeId && edge.branch_key === branchKey);
}

function conditionResultFromStep(step?: Step): ConditionResult | null {
  const input = step?.input;
  if (!isRecord(input) || !isRecord(input.condition_result)) return null;
  const result = input.condition_result;
  if (typeof result.chosen_branch !== 'string' || typeof result.reason !== 'string') return null;
  return {
    chosen_branch: result.chosen_branch,
    unchosen_branches: Array.isArray(result.unchosen_branches)
      ? result.unchosen_branches.filter((item): item is string => typeof item === 'string')
      : [],
    reason: result.reason,
    raw_answer: typeof result.raw_answer === 'string' ? result.raw_answer : null,
    forced: result.forced === true,
  };
}

function branchLabel(branch: string): string {
  return branch === CONDITION_DEFAULT_BRANCH_KEY ? '其它' : branch;
}

function RunTraceDialog({
  open,
  loading,
  error,
  trace,
  onClose,
}: {
  open: boolean;
  loading: boolean;
  error: string | null;
  trace: RunStepTrace | null;
  onClose(): void;
}) {
  const displayInput = trace ? stripPromptFromInput(trace.input) : null;
  return (
    <AppDialog
      open={open}
      onClose={onClose}
      title={trace ? `Trace · ${trace.node_title}` : 'Trace'}
      description="查看本次运行中该 LLM 节点实际收到的上下文、Prompt、Agent 过程和最终产物。"
      widthClassName="max-w-5xl"
    >
      <div className="max-h-[76vh] overflow-y-auto pr-1">
        {loading ? (
          <div className="rounded-xl border border-black/10 bg-black/[0.02] px-4 py-8 text-center text-sm text-black/50">
            正在加载 Trace…
          </div>
        ) : error ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : trace ? (
          <div className="space-y-5">
            <TraceMetaGrid trace={trace} />

            <TraceSection title="实际 Prompt">
              <TraceTextBlock value={trace.prompt || '未记录 prompt'} />
            </TraceSection>

            <TraceSection title="上下文与输入">
              <TraceTextBlock value={formatTraceValue(displayInput)} muted={!hasTraceValue(displayInput)} />
            </TraceSection>

            <TraceSection
              title="Agent 过程"
              action={trace.chunks_truncated ? <span className="text-amber-600">已截断</span> : null}
            >
              {trace.chunks.length ? (
                <ul className="divide-y divide-black/5 overflow-hidden rounded-xl border border-black/10">
                  {trace.chunks.map((chunk) => (
                    <li key={chunk.event_id} className="px-3 py-2 text-xs">
                      <div className="mb-1 flex items-center gap-2">
                        <span className={`rounded-full border px-2 py-0.5 ${traceChunkTone(chunk.type)}`}>
                          {chunk.type}
                        </span>
                        <span className="font-mono text-black/35">#{chunk.event_id}</span>
                      </div>
                      {chunk.text ? (
                        <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-black/70">
                          {chunk.text}
                        </pre>
                      ) : (
                        <span className="text-black/35">无文本内容</span>
                      )}
                      {chunk.raw ? (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-[11px] text-black/45 hover:text-black/70">
                            Raw JSON
                          </summary>
                          <TraceTextBlock value={formatTraceValue(chunk.raw)} compact />
                        </details>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-black/45">暂无 Agent 事件。</div>
              )}
              {trace.raw_text ? (
                <details className="mt-3">
                  <summary className="cursor-pointer text-xs text-black/50 hover:text-black/75">
                    查看拼接后的文本流
                  </summary>
                  <TraceTextBlock value={trace.raw_text} compact />
                </details>
              ) : null}
            </TraceSection>

            <TraceSection title="最终结果">
              <TraceTextBlock value={formatTraceValue(trace.output)} muted={!hasTraceValue(trace.output)} />
              {trace.error ? (
                <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  {trace.error}
                </div>
              ) : null}
            </TraceSection>

            <TraceSection
              title="Artifacts"
              action={trace.artifacts_truncated ? <span className="text-amber-600">已截断</span> : null}
            >
              {trace.artifacts.length ? (
                <ul className="divide-y divide-black/5 overflow-hidden rounded-xl border border-black/10">
                  {trace.artifacts.map((artifact) => (
                    <li key={artifact.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                      <span className="min-w-0 flex-1 truncate font-mono text-xs text-black/70">
                        {artifact.name}
                      </span>
                      <span className="shrink-0 font-mono text-xs text-black/40">
                        {formatBytes(artifact.size)}
                      </span>
                      <a
                        href={artifact.download_url}
                        download
                        className="shrink-0 rounded-full border border-black/10 px-2.5 py-1 text-xs font-medium text-black/65 hover:border-black/20 hover:text-black"
                      >
                        下载
                      </a>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-black/45">暂无文件产物。</div>
              )}
            </TraceSection>
          </div>
        ) : null}
      </div>
    </AppDialog>
  );
}

function TraceMetaGrid({ trace }: { trace: RunStepTrace }) {
  const items = [
    ['状态', STEP_LABEL[trace.status]],
    ['节点类型', llmNodeTypeLabel(trace.node_type)],
    ['模型', trace.model || '默认'],
    ['推理等级', trace.reasoning_effort || '默认'],
    ['耗时', formatDuration(trace.duration_ms ?? 0)],
    ['开始', trace.started_at ? formatTime(trace.started_at) : '未开始'],
  ];
  return (
    <section className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {items.map(([label, value]) => (
        <div key={label} className="rounded-xl border border-black/10 bg-black/[0.02] px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-black/40">{label}</div>
          <div className="mt-1 truncate font-mono text-xs text-black/75" title={value}>
            {value}
          </div>
        </div>
      ))}
    </section>
  );
}

function TraceSection({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <header className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-black/55">{title}</h3>
        {action ? <div className="text-xs">{action}</div> : null}
      </header>
      {children}
    </section>
  );
}

function TraceTextBlock({
  value,
  muted = false,
  compact = false,
}: {
  value: string;
  muted?: boolean;
  compact?: boolean;
}) {
  return (
    <pre
      className={`whitespace-pre-wrap break-words rounded-xl border border-black/10 bg-black/[0.025] font-mono text-[12px] leading-5 ${
        compact ? 'mt-2 max-h-72 overflow-y-auto px-3 py-2' : 'max-h-96 overflow-y-auto px-4 py-3'
      } ${muted ? 'text-black/40' : 'text-black/72'}`}
    >
      {value}
    </pre>
  );
}

function stripPromptFromInput(value: unknown): unknown {
  if (!isRecord(value)) return value;
  const next = { ...value };
  delete next.prompt;
  return next;
}

function hasTraceValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (isRecord(value)) return Object.keys(value).length > 0;
  return true;
}

function formatTraceValue(value: unknown): string {
  if (!hasTraceValue(value)) return '无';
  return formatOutputValue(value);
}

function traceChunkTone(type: RunStepTrace['chunks'][number]['type']): string {
  switch (type) {
    case 'tool_call':
      return 'border-indigo-200 bg-indigo-50 text-indigo-700';
    case 'tool_result':
      return 'border-emerald-200 bg-emerald-50 text-emerald-700';
    case 'error':
      return 'border-red-200 bg-red-50 text-red-700';
    case 'done':
      return 'border-black/10 bg-black/[0.03] text-black/50';
    default:
      return 'border-blue-200 bg-blue-50 text-blue-700';
  }
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function llmNodeTypeLabel(type: RunStepTrace['node_type']): string {
  switch (type) {
    case 'generate':
      return '生成';
    case 'output':
      return '输出';
    case 'condition':
      return '判断';
  }
}

function isLlmNode(node: WorkflowNode): node is LlmNode {
  return node.type === 'generate' || node.type === 'condition' || node.type === 'output';
}

function TimelineNodeActions({
  runId,
  node,
  onOpenTrace,
}: {
  runId: string | null;
  node: WorkflowNode;
  onOpenTrace(node: LlmNode): void;
}) {
  if (!runId || !isLlmNode(node)) return null;
  const nodeLabel = node.title || node.id;
  return (
    <button
      type="button"
      title="查看节点 Trace"
      aria-label={`查看 ${nodeLabel} 的 Trace`}
      onClick={(event) => {
        event.stopPropagation();
        onOpenTrace(node);
      }}
      className="inline-flex h-6 shrink-0 items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2.5 text-[11px] font-semibold text-blue-700 shadow-sm ring-1 ring-blue-100 transition hover:border-blue-300 hover:bg-blue-100 hover:text-blue-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
    >
      <Search className="h-3 w-3" />
      查看 Trace
    </button>
  );
}

interface TimelineViewProps {
  nodes: WorkflowNode[];
  steps: Record<string, Step>;
  deltas: Record<string, string>;
  runId: string | null;
  onOpenTrace(node: LlmNode): void;
  currentGraph: Graph | null;
  canStartBranchTest: boolean;
  branchTesting: string | null;
  branchTestError: string | null;
  onTestConditionBranch(node: ConditionNode, branchKey: string): void;
}

interface TimelineEntry {
  kind: 'log' | 'output' | 'condition';
  nodeId: string;
  nodeTitle: string;
  ts: string;
  node: WorkflowNode;
  log?: LogLine;
  text?: string;
  conditionResult?: ConditionResult;
}

function TimelineView({
  nodes,
  steps,
  deltas,
  runId,
  onOpenTrace,
  currentGraph,
  canStartBranchTest,
  branchTesting,
  branchTestError,
  onTestConditionBranch,
}: TimelineViewProps) {
  const [openOutputs, setOpenOutputs] = useState<Record<string, boolean>>({});
  const titleById = useMemo(() => {
    const m: Record<string, string> = {};
    nodes.forEach((n) => {
      m[n.id] = nodeLabel(n);
    });
    return m;
  }, [nodes]);

  const entries = useMemo<TimelineEntry[]>(() => {
    const list: TimelineEntry[] = [];
    nodes.forEach((n) => {
      const step = steps[n.id];
      step?.logs?.forEach((log) => {
        list.push({ kind: 'log', nodeId: n.id, nodeTitle: titleById[n.id] || n.id, ts: log.ts, node: n, log });
      });
      if (n.type === 'condition') {
        const conditionResult = conditionResultFromStep(step);
        if (conditionResult) {
          list.push({
            kind: 'condition',
            nodeId: n.id,
            nodeTitle: titleById[n.id] || n.id,
            ts: stepOutputTs(step),
            node: n,
            conditionResult,
          });
        }
      }
      if (
        (n.type === 'generate' || n.type === 'condition' || n.type === 'output') &&
        hasOutputValue(step?.output)
      ) {
        list.push({
          kind: 'output',
          nodeId: n.id,
          nodeTitle: titleById[n.id] || n.id,
          ts: stepOutputTs(step),
          node: n,
          text: formatOutputValue(step?.output),
        });
      }
    });
    list.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
    return list;
  }, [nodes, steps, titleById]);

  const runningDelta = useMemo(() => {
    for (const n of nodes) {
      const s = steps[n.id];
      if (s?.status === 'running') {
        const text = deltas[n.id];
        if (text) {
          return { nodeId: n.id, nodeTitle: titleById[n.id] || n.id, node: n, text };
        }
      }
    }
    return null;
  }, [nodes, steps, deltas, titleById]);

  if (entries.length === 0 && !runningDelta) {
    return <div className="text-sm text-black/45">暂无日志</div>;
  }

  return (
    <article className="overflow-hidden rounded-2xl border border-black/10 bg-white shadow-card">
      <ul className="divide-y divide-black/5">
        {entries.map((entry, idx) => {
          if (entry.kind === 'condition') {
            const result = entry.conditionResult;
            if (!result || entry.node.type !== 'condition') return null;
            return (
              <li key={`${entry.kind}-${entry.nodeId}-${idx}`} className="space-y-3 px-4 py-3 text-[12px]">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className="shrink-0 font-mono text-black/40">{formatTime(entry.ts)}</span>
                    <span className="shrink-0 rounded-full border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">
                      分支结果
                    </span>
                    <span className="max-w-40 shrink-0 truncate rounded-full border border-black/10 bg-white px-1.5 py-0.5 text-[10px] text-black/55">
                      {entry.nodeTitle}
                    </span>
                  </div>
                  <TimelineNodeActions runId={runId} node={entry.node} onOpenTrace={onOpenTrace} />
                </div>
                <ConditionResultSummary result={result} />
                <ConditionBranchTestPanel
                  node={entry.node}
                  currentGraph={currentGraph}
                  canStart={canStartBranchTest}
                  testingKey={branchTesting}
                  error={branchTestError}
                  onTestBranch={onTestConditionBranch}
                />
              </li>
            );
          }
          if (entry.kind === 'output') {
            const key = `output:${entry.nodeId}:${entry.ts}`;
            const open = !!openOutputs[key];
            const text = entry.text ?? '';
            return (
              <li key={`${entry.kind}-${entry.nodeId}-${idx}`} className="px-4 py-2 text-[12px]">
                <div className="flex w-full items-start gap-2">
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    <span className="shrink-0 font-mono text-black/40">{formatTime(entry.ts)}</span>
                    <span className="shrink-0 rounded-full border border-black/10 bg-black/[0.03] px-1.5 py-0.5 text-[10px] text-black/60">
                      AI 输出
                    </span>
                    <span className="max-w-40 shrink-0 truncate rounded-full border border-black/10 bg-white px-1.5 py-0.5 text-[10px] text-black/55">
                      {entry.nodeTitle}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setOpenOutputs((prev) => ({ ...prev, [key]: !open }))}
                    className="flex min-w-0 flex-1 items-start gap-2 text-left"
                    aria-expanded={open}
                  >
                    <span className="min-w-0 flex-1 break-words font-mono text-black/70">
                      {open ? '完整 AI 输出' : previewOutputText(text)}
                    </span>
                    <ChevronIcon open={open} className="mt-1 text-black/35" />
                  </button>
                  <TimelineNodeActions runId={runId} node={entry.node} onOpenTrace={onOpenTrace} />
                </div>
                {open ? (
                  <pre className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-black/[0.03] px-3 py-2 font-mono text-[12px] text-black/75">
                    {text}
                  </pre>
                ) : null}
              </li>
            );
          }
          const log = entry.log;
          if (!log) return null;
          const failed = log.level === 'error';
          return (
            <li
              key={`${entry.kind}-${entry.nodeId}-${idx}`}
              className={`flex gap-2 px-4 py-2 text-[12px] ${
                failed ? 'border-l-2 border-l-red-400' : ''
              }`}
            >
              <span className="shrink-0 font-mono text-black/40">{formatTime(log.ts)}</span>
              <span
                className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] ${levelChipTone(log.level)}`}
              >
                {log.level}
              </span>
              <span className="max-w-40 shrink-0 truncate rounded-full border border-black/10 bg-white px-1.5 py-0.5 text-[10px] text-black/55">
                {entry.nodeTitle}
              </span>
              <span
                className={`min-w-0 flex-1 whitespace-pre-wrap break-words ${levelColor(log.level)}`}
              >
                {log.text}
              </span>
              <TimelineNodeActions runId={runId} node={entry.node} onOpenTrace={onOpenTrace} />
            </li>
          );
        })}
        {runningDelta ? (
          <li className="bg-black/[0.02] px-4 py-2 text-[12px]">
            {(() => {
              const key = `running:${runningDelta.nodeId}`;
              const open = !!openOutputs[key];
              return (
                <>
                  <div className="flex w-full items-start gap-2">
                    <div className="flex shrink-0 flex-wrap items-center gap-2">
                      <span className="shrink-0 rounded-full border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] text-blue-700">
                        流式输出
                      </span>
                      <span className="max-w-40 shrink-0 truncate rounded-full border border-black/10 bg-white px-1.5 py-0.5 text-[10px] text-black/55">
                        {runningDelta.nodeTitle}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setOpenOutputs((prev) => ({ ...prev, [key]: !open }))}
                      className="flex min-w-0 flex-1 items-start gap-2 text-left"
                      aria-expanded={open}
                    >
                      <span className="min-w-0 flex-1 break-words font-mono text-black/70">
                        {open ? '完整实时输出' : previewOutputText(runningDelta.text)}
                      </span>
                      <ChevronIcon open={open} className="mt-1 text-black/35" />
                    </button>
                    <TimelineNodeActions runId={runId} node={runningDelta.node} onOpenTrace={onOpenTrace} />
                  </div>
                  {open ? (
                    <pre className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-black/[0.03] px-3 py-2 font-mono text-[12px] text-black/75">
                      {runningDelta.text}
                    </pre>
                  ) : null}
                </>
              );
            })()}
          </li>
        ) : null}
      </ul>
    </article>
  );
}
