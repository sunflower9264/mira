import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  CheckCircle2,
  History,
  Loader2,
  Play,
  RotateCcw,
  Settings2,
  Square,
} from 'lucide-react';
import * as api from '../../lib/api';
import { supportedModels } from '../../lib/modelOptions';
import {
  isCancellableRunStatus,
  isRestorableRunStatus,
  useRunStore,
  type RunUiStatus,
} from '../../stores/useRunStore';
import { useSettingsStore } from '../../stores/useSettingsStore';
import type {
  App,
  Run,
  RunSummary,
  Step,
  UserInputNode,
  WorkflowLintResult,
  WorkflowNode,
} from '../../types';
import { PillInputBar, type PillAttachment } from '../../components/common/PillInputBar';
import { SelectDropdown } from '../../components/common/SelectDropdown';
import { AppToolsInlineSelect, AppToolsSummary } from '../../components/common/AppToolsInlineSelect';
import { WorkflowLintNotice } from '../../components/common/WorkflowLintNotice';
import { WaitingInputPanel } from '../../components/preview/WaitingInputPanel';
import { HtmlOutputFrame } from '../../components/preview/HtmlOutputFrame';
import { RunArtifactsPanel, useRunArtifacts } from '../../components/preview/RunArtifactsPanel';
import { MobileSheet } from '../../components/mobile/MobileSheet';
import { useAppCoverUrl } from '../../hooks/useAppCoverUrl';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { useWikiAwareRunStart } from '../../hooks/useWikiAwareRunStart';

type View = 'result' | 'process';
type LaunchInputs = Record<string, string | { value: string; attachments?: { id: string; name?: string }[] }>;

const STATUS_LABEL: Record<RunUiStatus, string> = {
  idle: '待运行',
  starting: '启动中',
  pending: '排队中',
  running: '运行中',
  waiting_for_user: '等待输入',
  interrupted: '已中断',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
};
const EMPTY_NODES: WorkflowNode[] = [];

const STEP_LABEL: Record<Step['status'], string> = {
  pending: '等待中',
  running: '运行中',
  waiting_for_user: '等待输入',
  interrupted: '已中断',
  success: '成功',
  checkpoint_reused: '检查点复用',
  failed: '失败',
  skipped: '已跳过',
  cancelled: '已取消',
};

export function MobileRun() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [app, setApp] = useState<App | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [view, setView] = useState<View>('result');
  const [inputValue, setInputValue] = useState('');
  const [attachments, setAttachments] = useState<PillAttachment[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [lintResult, setLintResult] = useState<WorkflowLintResult | null>(null);
  const [lintLoading, setLintLoading] = useState(false);
  const [lintError, setLintError] = useState<string | null>(null);

  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);
  const resumeRun = useRunStore((s) => s.resume);
  const restoreActiveRun = useRunStore((s) => s.restoreActiveRun);
  const replayRun = useRunStore((s) => s.replay);
  const resetRun = useRunStore((s) => s.reset);
  const cancelRun = useRunStore((s) => s.cancel);
  const continueRun = useRunStore((s) => s.continueRun);
  const runId = useRunStore((s) => s.runId);
  const status = useRunStore((s) => s.status);
  const steps = useRunStore((s) => s.steps);
  const runGraph = useRunStore((s) => s.runGraph);
  const deltas = useRunStore((s) => s.deltas);
  const error = useRunStore((s) => s.error);
  const mode = useRunStore((s) => s.mode);
  const replay = useRunStore((s) => s.replayRun);
  const waitingInput = useRunStore((s) => s.waitingInput);
  const { start: startWithWiki, dialog: wikiAccessDialog } = useWikiAwareRunStart(app, () => setView('process'));

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    if (!id) return;
    void api
      .getApp(id)
      .then((data) => {
        if (!cancelled) setApp(data);
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : '应用加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      resetRun();
    };
  }, [id, resetRun]);

  useEffect(() => {
    if (!app || runId !== null) return;
    void restoreActiveRun(app);
  }, [app, runId, restoreActiveRun]);

  useEffect(() => {
    if (!app) return;
    const controller = new AbortController();
    setLintLoading(true);
    setLintError(null);
    void api.lintAppGraph(app.id, app.can_view_source ? app.graph : undefined, controller.signal)
      .then((result) => {
        setLintResult(result);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setLintError(err instanceof Error ? err.message : '预检失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLintLoading(false);
      });
    return () => controller.abort();
  }, [app]);

  const appGraphNodes = app?.graph.nodes ?? EMPTY_NODES;
  const runGraphNodes = runGraph?.nodes ?? appGraphNodes;
  const activeInput = useMemo(() => findActiveInput(app), [app]);
  const outputs = useMemo(() => runGraphNodes.filter((n) => n.type === 'output'), [runGraphNodes]);
  const progress = useMemo(() => progressFor(runGraphNodes, steps), [runGraphNodes, steps]);
  const outputReady = outputs.some((node) => {
    const step = steps[node.id];
    return step?.status === 'success' || step?.status === 'failed' || step?.status === 'skipped';
  });
  const canStop = runId !== null && isCancellableRunStatus(status);
  const running = runId !== null && !['success', 'failed', 'cancelled', 'interrupted'].includes(status);
  const showIdle = status === 'idle';
  const activeFilled =
    !activeInput ||
    inputForNode(activeInput, inputValue, attachments).filled;
  const hasLintErrors = (lintResult?.summary.errors ?? 0) > 0;
  const canStart = !!app && app.can_run && appGraphNodes.length > 0 && activeFilled && !submitting && !hasLintErrors;

  const start = async () => {
    if (!app || !canStart) return;
    setSubmitting(true);
    try {
      const inputs: LaunchInputs = {};
      if (activeInput) {
        const uploaded = attachments.length ? await uploadAttachments(attachments, setAttachments) : [];
        inputs[activeInput.id] = uploaded.length ? { value: inputValue, attachments: uploaded } : inputValue;
      }
      await startWithWiki(inputs);
    } catch (err) {
      showCaughtError(err, '启动运行失败', '启动失败');
    } finally {
      setSubmitting(false);
    }
  };

  const restartFromCurrentRun = async () => {
    if (!app || !runId) {
      resetRun();
      return;
    }
    const previous = await api.getRun(runId);
    resetRun();
    await startWithWiki(previous.inputs);
  };

  if (loading) {
    return (
      <MobilePageFrame title="Mira">
        <div className="grid min-h-[60dvh] place-items-center text-sm text-black/45">加载中...</div>
      </MobilePageFrame>
    );
  }

  if (!app || loadError) {
    return (
      <MobilePageFrame title="Mira">
        <div className="px-4 py-8">
          <div className="rounded-[22px] border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            {loadError ?? '应用不存在'}
          </div>
        </div>
      </MobilePageFrame>
    );
  }

  return (
    <div className="flex min-h-dvh flex-col bg-[#F4F5F7] text-[#0B0B0F]">
      <header className="sticky top-0 z-20 border-b border-black/5 bg-white/85 px-3 pb-2 pt-[calc(env(safe-area-inset-top)+8px)] backdrop-blur">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => navigate('/m')}
            className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-black/55 hover:bg-black/5 hover:text-black"
            aria-label="返回"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div className="min-w-0 flex-1">
            <div className="truncate text-base font-semibold">{app.name}</div>
            <div className="mt-0.5 flex items-center gap-2 text-[11px] text-black/45">
              <span>{STATUS_LABEL[status]}</span>
              {!app.can_view_source ? <><span>·</span><span>仅运行</span></> : null}
            </div>
          </div>
          <button
            type="button"
            onClick={() => setHistoryOpen(true)}
            className="grid h-10 w-10 place-items-center rounded-full text-black/55 hover:bg-black/5 hover:text-black"
            aria-label="历史记录"
          >
            <History className="h-4 w-4" />
          </button>
          {app.can_edit ? (
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="grid h-10 w-10 place-items-center rounded-full text-black/55 hover:bg-black/5 hover:text-black"
              aria-label="运行设置"
            >
              <Settings2 className="h-4 w-4" />
            </button>
          ) : null}
        </div>
        <div className="mt-2 h-1 overflow-hidden rounded-full bg-black/[0.06]">
          <div className="h-full rounded-full bg-black/70 transition-[width] duration-300" style={{ width: `${progress.percent}%` }} />
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-4 pb-4 pt-4">
        {mode === 'replay' && replay ? (
          <div className="mb-3 rounded-2xl border border-black/10 bg-white px-3 py-2 text-xs text-black/60 shadow-card">
            正在查看历史记录 · {formatDate(replay.started_at)}
          </div>
        ) : null}

        {showIdle ? (
          <IdlePanel
            app={app}
            lintResult={lintResult}
            lintLoading={lintLoading}
            lintError={lintError}
          />
        ) : status === 'starting' ? (
          <StartingPanel />
        ) : waitingInput ? (
          <div className="min-h-[60dvh]">
            <WaitingInputPanel />
          </div>
        ) : status === 'interrupted' ? (
          <InterruptedPanel onContinue={() => void continueRun()} onRestart={() => void restartFromCurrentRun()} />
        ) : (
          <>
            {outputReady || status === 'success' || status === 'failed' || status === 'cancelled' ? (
              <Segmented value={view} onChange={setView} />
            ) : null}
            {view === 'result' && (outputReady || status === 'success' || status === 'failed' || status === 'cancelled') ? (
              <ResultView outputs={outputs} steps={steps} status={status} error={error} runId={runId} />
            ) : (
              <ProcessView nodes={runGraphNodes} steps={steps} deltas={deltas} progress={progress} status={status} />
            )}
          </>
        )}

      </main>

      <footer className="shrink-0 border-t border-black/5 bg-white/90 px-4 pb-[calc(env(safe-area-inset-bottom)+12px)] pt-3 backdrop-blur">
        {showIdle ? (
          <StartComposer
            input={activeInput}
            inputValue={inputValue}
            onInputValueChange={setInputValue}
            attachments={attachments}
            onAttachmentsChange={setAttachments}
            canStart={canStart}
            submitting={submitting}
            onStart={() => void start()}
          />
        ) : status === 'starting' ? (
          <button
            type="button"
            disabled
            className="flex h-11 w-full items-center justify-center gap-2 rounded-full bg-black text-sm font-medium text-white opacity-70"
          >
            <Loader2 className="h-4 w-4 animate-spin" />
            启动中...
          </button>
        ) : canStop ? (
          <button
            type="button"
            onClick={() => void cancelRun()}
            className="flex h-11 w-full items-center justify-center gap-2 rounded-full border border-red-200 bg-red-50 text-sm font-medium text-red-700"
          >
            <Square className="h-4 w-4 fill-current" />
            停止运行
          </button>
        ) : (
          <div className="grid grid-cols-[1fr_auto] gap-2">
            <button
              type="button"
              disabled={!app?.can_run}
              onClick={() => {
                resetRun();
                setInputValue('');
                setAttachments([]);
                setView('result');
              }}
              className="flex h-11 items-center justify-center gap-2 rounded-full bg-black text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              <RotateCcw className="h-4 w-4" />
              新运行
            </button>
            {running ? (
              <button type="button" className="h-11 rounded-full border border-black/10 px-4 text-sm text-black/55">
                {STATUS_LABEL[status]}
              </button>
            ) : null}
          </div>
        )}
      </footer>

      <MobileRunHistorySheet
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        app={app}
        currentRunId={runId}
        onSelectRun={(run) => {
          if (isRestorableRunStatus(run.status)) {
            resumeRun(app, run);
            setView('process');
          } else {
            replayRun(run);
            setView('result');
          }
          setHistoryOpen(false);
        }}
      />
      {app.can_edit ? (
        <MobileRunSettingsSheet
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          app={app}
          onAppChange={setApp}
        />
      ) : null}
      {wikiAccessDialog}
    </div>
  );
}

function MobilePageFrame({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-h-dvh bg-[#F4F5F7] text-[#0B0B0F]">
      <header className="border-b border-black/5 bg-white/85 px-4 pb-3 pt-[calc(env(safe-area-inset-top)+12px)] backdrop-blur">
        <div className="text-lg font-semibold">{title}</div>
      </header>
      {children}
    </div>
  );
}

function IdlePanel({
  app,
  lintResult,
  lintLoading,
  lintError,
}: {
  app: App;
  lintResult: WorkflowLintResult | null;
  lintLoading: boolean;
  lintError: string | null;
}) {
  const coverUrl = useAppCoverUrl(app);
  return (
    <section className="space-y-4">
      <div
        className="aspect-video overflow-hidden rounded-[24px] bg-neutral-950 p-4 text-white shadow-card"
        style={
          coverUrl
            ? {
                backgroundImage: `linear-gradient(180deg, rgba(0,0,0,0.12), rgba(0,0,0,0.82)), url(${coverUrl})`,
                backgroundPosition: 'center',
                backgroundSize: 'cover',
              }
            : undefined
        }
      >
        <div className="flex h-full flex-col justify-end">
          <div className="mb-2 flex items-center gap-2 text-[10px] text-white/65">
            <span className="rounded-full bg-white/15 px-2 py-0.5 backdrop-blur">
              {app.archived_at ? '已下架' : app.status === 'published' ? '已发布' : '草稿'}
            </span>
            {app.can_view_source ? <span>{app.graph.nodes.length} nodes</span> : null}
          </div>
          <h1 className="text-xl font-semibold leading-tight">{app.name}</h1>
          <p className="mt-2 line-clamp-2 text-sm leading-5 text-white/68">
            {app.description || '输入内容后运行这个 Mira 应用。'}
          </p>
        </div>
      </div>
      {app.can_view_source ? <AppToolsSummary disabledToolIds={app.graph.tools?.disabled_tool_ids ?? []} /> : null}
      {!app.can_run ? (
        <Notice tone="neutral">应用已下架，只能查看历史运行记录。</Notice>
      ) : app.graph.nodes.length === 0 ? (
        <Notice tone="warn">这个应用还没有节点，需要在桌面端编辑后才能运行。</Notice>
      ) : !app.can_view_source ? (
        <Notice tone="neutral">这是仅运行应用，发布者未开放节点查看和克隆。</Notice>
      ) : null}
      <WorkflowLintNotice result={lintResult} loading={lintLoading} error={lintError} />
    </section>
  );
}

function StartingPanel() {
  return (
    <section className="rounded-[22px] border border-blue-200 bg-blue-50 p-4 text-blue-900 shadow-card">
      <div className="flex items-center gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-full bg-blue-600 text-white">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
        <div>
          <div className="text-sm font-semibold">正在创建运行</div>
          <div className="mt-1 text-xs text-blue-700">拿到 run_id 后会开始接收实时事件。</div>
        </div>
      </div>
    </section>
  );
}

function Notice({ tone, children }: { tone: 'neutral' | 'warn'; children: React.ReactNode }) {
  const cls = tone === 'warn' ? 'border-amber-200 bg-amber-50 text-amber-800' : 'border-black/10 bg-white text-black/60';
  return <div className={`rounded-2xl border px-4 py-3 text-sm leading-6 shadow-card ${cls}`}>{children}</div>;
}

function StartComposer({
  input,
  inputValue,
  onInputValueChange,
  attachments,
  onAttachmentsChange,
  canStart,
  submitting,
  onStart,
}: {
  input: UserInputNode | null;
  inputValue: string;
  onInputValueChange(value: string): void;
  attachments: PillAttachment[];
  onAttachmentsChange(value: PillAttachment[]): void;
  canStart: boolean;
  submitting: boolean;
  onStart(): void;
}) {
  if (!input) {
    return (
      <button
        type="button"
        onClick={onStart}
        disabled={!canStart}
        className="flex h-11 w-full items-center justify-center gap-2 rounded-full bg-black text-sm font-medium text-white disabled:opacity-40"
      >
        {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
        {submitting ? '启动中...' : '开始运行'}
      </button>
    );
  }

  return (
    <PillInputBar
      value={inputValue}
      onChange={onInputValueChange}
      onSubmit={onStart}
      placeholder={input.input_schema.placeholder ?? input.input_schema.label ?? '输入内容...'}
      canSubmit={canStart || attachments.length > 0}
      submitting={submitting}
      allowAttachments
      attachments={attachments}
      onAttachmentsChange={onAttachmentsChange}
      ariaLabel="开始运行"
    />
  );
}

function ProcessView({
  nodes,
  steps,
  deltas,
  progress,
  status,
}: {
  nodes: WorkflowNode[];
  steps: Record<string, Step>;
  deltas: Record<string, string>;
  progress: ReturnType<typeof progressFor>;
  status: RunUiStatus;
}) {
  const current = nodes.find((node) => {
    const s = steps[node.id]?.status;
    return s === 'running' || s === 'waiting_for_user';
  });
  const summary = processSummaryLabel(status, progress.done, progress.total, progress.percent);
  const finished = status === 'success' || status === 'failed' || status === 'cancelled' || status === 'interrupted';

  return (
    <section className="space-y-3">
      <div className="rounded-[22px] border border-black/10 bg-white p-4 shadow-card">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center rounded-full bg-black text-white">
            {finished ? <CheckCircle2 className="h-5 w-5" /> : <Loader2 className="h-5 w-5 animate-spin" />}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{current?.title || '运行流程'}</div>
            <div className="mt-0.5 text-xs text-black/45">{summary}</div>
          </div>
        </div>
      </div>
      {nodes.map((node) => (
        <StepCard key={node.id} node={node} step={steps[node.id]} delta={deltas[node.id] ?? ''} />
      ))}
    </section>
  );
}

function processSummaryLabel(status: RunUiStatus, done: number, total: number, percent: number): string {
  switch (status) {
    case 'success':
      return `已完成 ${done}/${total} · ${percent}%`;
    case 'failed':
      return '运行失败';
    case 'interrupted':
      return '已中断';
    case 'cancelled':
      return '已取消';
    default:
      return `进度 ${done}/${total} · ${percent}%`;
  }
}

function StepCard({ node, step, delta }: { node: WorkflowNode; step?: Step; delta: string }) {
  const status = step?.status ?? 'pending';
  const outputText = formatOutputValue(step?.output) || delta.trim();
  return (
    <details open={status === 'running' || status === 'failed'} className="rounded-[20px] border border-black/10 bg-white shadow-card">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3">
        <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${stepDot(status)}`} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-black/85">{node.title || node.id}</div>
          <div className="mt-0.5 text-xs text-black/45">{nodeTypeLabel(node.type)} · {STEP_LABEL[status]}</div>
        </div>
        <span className="text-[11px] text-black/35">{formatDuration(step?.duration_ms)}</span>
      </summary>
      <div className="space-y-3 border-t border-black/5 px-4 py-3">
        {outputText ? (
          <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-2xl bg-black/[0.03] p-3 font-mono text-xs leading-5 text-black/70">
            {previewText(outputText, 1200)}
          </pre>
        ) : (
          <div className="text-xs text-black/40">暂无输出。</div>
        )}
        {step?.logs?.length ? (
          <ul className="space-y-1 font-mono text-[11px] text-black/58">
            {step.logs.slice(-8).map((log, index) => (
              <li key={`${log.ts}-${index}`} className="whitespace-pre-wrap break-words">
                <span className="text-black/35">{formatLogTime(log.ts)}</span> {log.text}
              </li>
            ))}
          </ul>
        ) : null}
        {step?.error ? (
          <pre className="whitespace-pre-wrap break-words rounded-2xl bg-red-50 p-3 font-mono text-xs text-red-700">
            {step.error}
          </pre>
        ) : null}
      </div>
    </details>
  );
}

function ResultView({
  outputs,
  steps,
  status,
  error,
  runId,
}: {
  outputs: WorkflowNode[];
  steps: Record<string, Step>;
  status: Run['status'];
  error: string | null;
  runId: string | null;
}) {
  const artifactsState = useRunArtifacts(runId, status);

  return (
    <section className="space-y-3">
      {status === 'failed' && error ? (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <div className="mb-1 font-semibold">运行失败</div>
          <div className="whitespace-pre-wrap break-words">{error}</div>
        </div>
      ) : null}
      {outputs.map((node) => {
        const step = steps[node.id];
        const text = formatOutputValue(step?.output);
        return (
          <article key={node.id} className="overflow-hidden rounded-[22px] border border-black/10 bg-white shadow-card">
            <header className="flex items-center justify-between gap-2 border-b border-black/5 px-4 py-3">
              <div className="truncate text-sm font-semibold">{node.title || '输出'}</div>
              <span className="rounded-full bg-black/[0.04] px-2 py-0.5 text-[10px] text-black/45">
                {STEP_LABEL[step?.status ?? 'pending']}
              </span>
            </header>
            {text ? (
              <HtmlOutputFrame
                html={text}
                artifacts={artifactsState.artifacts}
                title={node.title || '输出'}
                className="block h-[62dvh] w-full border-0 bg-white"
              />
            ) : (
              <div className="px-4 py-10 text-center text-sm text-black/45">
                {step?.status === 'skipped' ? '已跳过' : '尚未产出内容'}
              </div>
            )}
            {step?.error ? (
              <div className="border-t border-red-100 bg-red-50 px-4 py-2 text-xs text-red-700">{step.error}</div>
            ) : null}
          </article>
        );
      })}
      {outputs.length === 0 ? (
        <div className="rounded-2xl border border-black/10 bg-white px-4 py-10 text-center text-sm text-black/45 shadow-card">
          这个应用没有输出节点。
        </div>
      ) : null}
      <RunArtifactsPanel runId={runId} density="mobile" state={artifactsState} />
    </section>
  );
}

function InterruptedPanel({ onContinue, onRestart }: { onContinue(): void; onRestart(): void }) {
  return (
    <section className="rounded-[22px] border border-amber-200 bg-amber-50 p-4 text-amber-900 shadow-card">
      <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-amber-700">运行已中断</div>
      <h2 className="mt-2 text-lg font-semibold">可以从未完成节点继续</h2>
      <p className="mt-2 text-sm leading-6 text-amber-800">
        继续运行会跳过已完成节点；中断节点可能会恢复同一 Agent 会话。
      </p>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <button type="button" onClick={onContinue} className="h-10 rounded-full bg-black text-sm font-medium text-white">继续运行</button>
        <button type="button" onClick={onRestart} className="h-10 rounded-full border border-amber-300 bg-white text-sm font-medium text-amber-900">重新运行</button>
      </div>
    </section>
  );
}

function Segmented({ value, onChange }: { value: View; onChange(value: View): void }) {
  return (
    <div className="mb-3 grid grid-cols-2 rounded-full bg-black/[0.05] p-1 text-sm">
      <button type="button" onClick={() => onChange('result')} className={`h-9 rounded-full ${value === 'result' ? 'bg-white font-medium shadow-sm' : 'text-black/50'}`}>结果</button>
      <button type="button" onClick={() => onChange('process')} className={`h-9 rounded-full ${value === 'process' ? 'bg-white font-medium shadow-sm' : 'text-black/50'}`}>过程</button>
    </div>
  );
}

function MobileRunHistorySheet({
  open,
  onOpenChange,
  app,
  currentRunId,
  onSelectRun,
}: {
  open: boolean;
  onOpenChange(open: boolean): void;
  app: App;
  currentRunId: string | null;
  onSelectRun(run: Run): void;
}) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectingId, setSelectingId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setSelectingId(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void api
      .listRunSummaries(app.id)
      .then((data) => {
        if (!cancelled) setRuns(data);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, app.id]);

  const selectRun = async (id: string) => {
    setSelectingId(id);
    try {
      const run = await api.getRun(id);
      onSelectRun(run);
    } catch (error) {
      showCaughtError(error, '加载历史运行失败', '加载历史运行失败');
    } finally {
      setSelectingId(null);
    }
  };

  return (
    <MobileSheet open={open} onOpenChange={onOpenChange} title="历史记录">
      {loading && !runs ? <div className="py-8 text-center text-sm text-black/45">加载中...</div> : null}
      {runs?.length === 0 ? (
        <div className="py-10 text-center text-sm text-black/45">暂无运行记录</div>
      ) : null}
      <div className="space-y-2">
        {(runs ?? []).map((run) => (
          <button
            key={run.id}
            type="button"
            disabled={selectingId !== null}
            onClick={() => void selectRun(run.id)}
            className={`w-full rounded-2xl border px-3 py-3 text-left ${
              run.id === currentRunId ? 'border-black/35 bg-black/[0.04]' : 'border-black/10 bg-white'
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium text-black/80">
                {selectingId === run.id ? '加载中...' : run.name || summarizeInputs(run.inputs) || app.name}
              </span>
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${statusTone(run.status)}`}>{STATUS_LABEL[run.status]}</span>
            </div>
            <div className="mt-1 text-xs text-black/42">{formatDate(run.started_at)}</div>
          </button>
        ))}
      </div>
    </MobileSheet>
  );
}

function MobileRunSettingsSheet({
  open,
  onOpenChange,
  app,
  onAppChange,
}: {
  open: boolean;
  onOpenChange(open: boolean): void;
  app: App;
  onAppChange(app: App): void;
}) {
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);
  const [model, setModel] = useState('');
  const [disabledToolIds, setDisabledToolIds] = useState<string[]>(app.graph.tools?.disabled_tool_ids ?? []);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  useEffect(() => {
    if (!open) return;
    setModel(commonModel(app.graph.nodes));
    setDisabledToolIds(app.graph.tools?.disabled_tool_ids ?? []);
  }, [open, app]);

  const models = supportedModels(settings);

  const save = async () => {
    setSaving(true);
    try {
      const nextGraph = {
        ...app.graph,
        tools: { disabled_tool_ids: disabledToolIds },
        nodes: app.graph.nodes.map((node) => {
          if (node.type === 'generate') {
            return {
              ...node,
              model: model || undefined,
            };
          }
          if (node.type === 'condition' || node.type === 'output') {
            return { ...node, model: model || undefined };
          }
          return node;
        }),
      };
      const updated = await api.patchApp(app.id, { graph: nextGraph });
      onAppChange(updated);
      onOpenChange(false);
    } catch (err) {
      showCaughtError(err, '保存运行设置失败', '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <MobileSheet open={open} onOpenChange={onOpenChange} title="运行设置">
      <div className="space-y-5">
        <div>
          <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-black/45">模型</span>
          <SelectDropdown
            value={model}
            disabled={models.length === 0}
            options={[
              { label: '使用 Codex 默认模型', value: '' },
              ...models.map((item) => ({ label: item, value: item })),
            ]}
            onChange={setModel}
            className="mt-2"
            buttonClassName={mobileSelectButtonCls}
            menuClassName={mobileSelectMenuCls}
          />
        </div>

        <div>
          <AppToolsInlineSelect
            disabledToolIds={disabledToolIds}
            onChange={setDisabledToolIds}
            density="mobile"
          />
        </div>

        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className="h-11 w-full rounded-full bg-black text-sm font-medium text-white disabled:opacity-45"
        >
          {saving ? '保存中...' : '保存设置'}
        </button>
      </div>
    </MobileSheet>
  );
}

function findActiveInput(app: App | null): UserInputNode | null {
  if (!app || app.graph.nodes.length === 0) return null;
  return app.graph.nodes.find((node): node is UserInputNode => node.type === 'user_input') ?? null;
}

function inputForNode(node: UserInputNode, text: string, attachments: PillAttachment[]) {
  if (node.input_schema.kind === 'file') return { filled: attachments.length > 0 || text.trim().length > 0 };
  return { filled: text.trim().length > 0 || attachments.length > 0 };
}

const mobileSelectButtonCls = 'flex h-11 w-full items-center rounded-2xl border border-black/10 bg-white px-3 text-left text-sm outline-none transition hover:bg-black/[0.02] focus:border-black/35';
const mobileSelectMenuCls = 'absolute left-0 right-0 top-full z-30 mt-1 max-h-60 overflow-y-auto rounded-2xl border border-black/10 bg-white p-1 shadow-lg';

async function uploadAttachments(
  attachments: PillAttachment[],
  setAttachments: (attachments: PillAttachment[]) => void,
): Promise<{ id: string; name?: string }[]> {
  const refs: { id: string; name?: string }[] = [];
  const next = [...attachments];
  for (let i = 0; i < next.length; i += 1) {
    const item = next[i];
    if (item.uploadId) {
      refs.push({ id: item.uploadId, name: item.name });
      continue;
    }
    if (!item.file) throw new Error(`附件「${item.name}」缺少文件内容`);
    const uploaded = await api.uploadFile(item.file);
    next[i] = { ...item, uploadId: uploaded.id };
    refs.push({ id: uploaded.id, name: item.name });
  }
  setAttachments(next);
  return refs;
}

function progressFor(nodes: WorkflowNode[], steps: Record<string, Step>) {
  const total = nodes.length;
  const done = nodes.filter((node) => {
    const status = steps[node.id]?.status;
    return status === 'success' || status === 'failed' || status === 'skipped' || status === 'cancelled' || status === 'interrupted';
  }).length;
  return { done, total, percent: total ? Math.round((done / total) * 100) : 0 };
}

function commonModel(nodes: WorkflowNode[]): string {
  const promptNodes = nodes.filter((node) => node.type === 'generate' || node.type === 'condition' || node.type === 'output');
  const values = new Set(promptNodes.map((node) => ('model' in node ? node.model ?? '' : '')));
  return values.size === 1 ? values.values().next().value ?? '' : '';
}

function formatOutputValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === null || value === undefined) return '';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function previewText(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function nodeTypeLabel(type: WorkflowNode['type']): string {
  switch (type) {
    case 'user_input':
      return '用户输入';
    case 'generate':
      return '生成';
    case 'output':
      return '输出';
    case 'asset':
      return '素材';
    case 'condition':
      return '判断';
  }
}

function stepDot(status: Step['status']): string {
  switch (status) {
    case 'running':
      return 'bg-blue-500 animate-pulse';
    case 'waiting_for_user':
      return 'bg-amber-500 animate-pulse';
    case 'success':
      return 'bg-emerald-500';
    case 'failed':
      return 'bg-red-500';
    case 'cancelled':
    case 'interrupted':
      return 'bg-amber-500';
    default:
      return 'bg-black/15';
  }
}

function statusTone(status: Run['status']): string {
  switch (status) {
    case 'success':
      return 'bg-emerald-50 text-emerald-700';
    case 'failed':
      return 'bg-red-50 text-red-700';
    case 'running':
      return 'bg-blue-50 text-blue-700';
    case 'waiting_for_user':
    case 'interrupted':
      return 'bg-amber-50 text-amber-700';
    default:
      return 'bg-black/[0.04] text-black/50';
  }
}

function formatDuration(ms: number | undefined): string {
  if (!ms) return '';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatLogTime(iso: string): string {
  return iso.length >= 19 ? iso.slice(11, 19) : iso;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function summarizeInputs(inputs: Run['inputs']): string {
  for (const value of Object.values(inputs ?? {})) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (value && typeof value === 'object') {
      const record = value as { value?: unknown; attachments?: unknown };
      if (typeof record.value === 'string' && record.value.trim()) return record.value.trim();
      if (Array.isArray(record.attachments)) {
        const first = record.attachments.find((item) => item && typeof item === 'object' && typeof (item as { name?: unknown }).name === 'string');
        if (first) return String((first as { name: string }).name);
      }
    }
  }
  return '';
}
