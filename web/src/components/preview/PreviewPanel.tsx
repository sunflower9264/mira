// Right-hand panel (PRD §7.2). Three tabs: Preview / Console / Step.

import { useEffect, useMemo, useState } from 'react';
import { PreviewTab } from './PreviewTab';
import { ConsoleTab } from './ConsoleTab';
import { StepTab } from './StepTab';
import { RefreshIcon, MenuIcon, EditIcon } from '../common/Icons';
import { isRestorableRunStatus, useRunStore } from '../../stores/useRunStore';
import { useEditorStore } from '../../stores/useEditorStore';
import { RunHistoryDrawer } from './RunHistoryDrawer';
import { useRunProgress } from './useRunProgress';
import { AppDialog } from '../common/AppDialog';
import { RunFailureError } from './AppRunContent';
import * as api from '../../lib/api';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import type { App, Run, UserInputNode } from '../../types';
import type { LaunchInputs } from './AppLaunchView';

type Tab = 'preview' | 'console' | 'step';
const TAB_LABELS: Record<Tab, string> = {
  preview: '预览',
  console: '控制台',
  step: '步骤',
};

export function PreviewPanel() {
  const [tab, setTab] = useState<Tab>('preview');
  const [historyOpen, setHistoryOpen] = useState(false);
  const status = useRunStore((s) => s.status);
  const steps = useRunStore((s) => s.steps);
  const runError = useRunStore((s) => s.error);
  const runGraph = useRunStore((s) => s.runGraph);
  const reset = useRunStore((s) => s.reset);
  const replay = useRunStore((s) => s.replay);
  const resume = useRunStore((s) => s.resume);
  const rerunFrom = useRunStore((s) => s.rerunFrom);
  const runId = useRunStore((s) => s.runId);
  const mode = useRunStore((s) => s.mode);
  const app = useEditorStore((s) => s.app);
  const flushSave = useEditorStore((s) => s.flushSave);
  const setSelected = useEditorStore((s) => s.setSelected);
  const focusCanvasNode = useEditorStore((s) => s.focusCanvasNode);
  const selectedIds = useEditorStore((s) => s.selectedIds);
  const progress = useRunProgress();
  const stepEnabled = selectedIds.length === 1;
  const selectedNodeId = selectedIds[0] ?? null;
  const [rerunning, setRerunning] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [inputDialogOpen, setInputDialogOpen] = useState(false);
  const [repairInputs, setRepairInputs] = useState<LaunchInputs | null>(null);
  const selectedNode = app?.graph.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const failedStep = useMemo(
    () => Object.values(steps).find((step) => step.status === 'failed') ?? null,
    [steps],
  );
  const failedNodeInRun = failedStep
    ? runGraph?.nodes.find((node) => node.id === failedStep.node_id) ?? null
    : null;
  const failedNodeInApp = failedStep && app
    ? app.graph.nodes.find((node) => node.id === failedStep.node_id) ?? null
    : null;
  const canRepairFailedRun =
    app !== null &&
    runId !== null &&
    failedStep !== null &&
    (mode === 'replay' || status === 'failed');
  const showRepairFailureError = canRepairFailedRun && !!runError;
  const canRerunFromSelected =
    app !== null &&
    runId !== null &&
    selectedNode !== null &&
    (mode === 'replay' || status === 'success' || status === 'failed' || status === 'cancelled');

  useEffect(() => {
    if (stepEnabled && runId === null && mode !== 'replay') setTab('step');
  }, [selectedNodeId, stepEnabled, runId, mode]);

  const editFailedNode = () => {
    if (!failedStep || !failedNodeInApp) return;
    setSelected(failedStep.node_id);
    focusCanvasNode(failedStep.node_id);
    setTab('step');
  };

  const rerunFromFailedNode = () => {
    if (!app || !runId || !failedStep || !failedNodeInApp) return;
    setRepairing(true);
    void flushSave()
      .then(() => rerunFrom(runId, app, failedStep.node_id, repairInputs ?? undefined))
      .then(() => {
        setTab('preview');
        setRepairInputs(null);
      })
      .catch((error) => {
        showCaughtError(error, '修复运行启动失败', '启动失败');
      })
      .finally(() => setRepairing(false));
  };

  return (
    <div className="relative h-full flex flex-col overflow-hidden">
      <header className="h-12 px-3 flex items-center border-b border-black/5">
        <button
          className="p-1.5 rounded-full hover:bg-black/5 text-black/55"
          aria-label="历史记录"
          onClick={() => setHistoryOpen(true)}
        >
          <MenuIcon className="w-4 h-4" />
        </button>
        <div className="flex-1 flex items-center justify-center gap-1 text-sm">
          {(['preview', 'console', 'step'] as Tab[]).map((t) => {
            const disabled = t === 'step' && !stepEnabled;
            return (
              <button
                key={t}
                onClick={() => {
                  if (!disabled) setTab(t);
                }}
                disabled={disabled}
                className={`px-3 py-1 rounded-full ${
                  disabled
                    ? 'text-black/25 cursor-not-allowed'
                    : tab === t
                      ? 'bg-black/5 text-black'
                      : 'text-black/45 hover:text-black'
                }`}
              >
                {TAB_LABELS[t]}
              </button>
            );
          })}
        </div>
        <button
          aria-label="重启预览"
          title="重启预览"
          className="p-1.5 rounded-full text-black/55 hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-40"
          onClick={() => reset()}
          disabled={status !== 'success' && status !== 'failed' && status !== 'interrupted'}
        >
          <RefreshIcon className="w-4 h-4" />
        </button>
      </header>
      {/* Progress bar (PRD §7.3) */}
      <div className="h-1 bg-black/5 relative">
        <div
          className="absolute inset-y-0 left-0 bg-black/55 transition-[width] duration-200"
          style={{ width: `${progress * 100}%` }}
        />
      </div>
      {canRepairFailedRun && failedStep && (
        <>
          <FailedRunRepairBar
            nodeTitle={failedNodeInApp?.title || failedNodeInRun?.title || failedStep.node_id}
            nodeExists={!!failedNodeInApp}
            hasInputOverrides={repairInputs !== null}
            repairing={repairing}
            onEditNode={editFailedNode}
            onEditInputs={() => setInputDialogOpen(true)}
            onRerun={rerunFromFailedNode}
          />
          {runError ? <RunFailureError error={runError} className="mx-3 my-2 shrink-0" /> : null}
        </>
      )}
      {canRerunFromSelected && app && runId && selectedNode && (
        <div className="flex items-center justify-between gap-2 border-b border-black/5 bg-white px-3 py-2">
          <div className="min-w-0 text-xs text-black/50">
            <span className="text-black/70">已选择</span>
            <span className="mx-1 truncate font-medium text-black/75">{selectedNode.title || selectedNode.id}</span>
          </div>
          <button
            type="button"
            disabled={rerunning}
            onClick={() => {
              setRerunning(true);
              void flushSave()
                .then(() => rerunFrom(runId, app, selectedNode.id))
                .then(() => setTab('preview'))
                .catch((error) => showCaughtError(error, '重新执行失败', '重新执行失败'))
                .finally(() => setRerunning(false));
            }}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-black/10 bg-white px-3 py-1.5 text-xs font-medium text-black/70 hover:bg-black/[0.03] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <RefreshIcon className="h-3.5 w-3.5" />
            {rerunning ? '启动中' : '从此检查点重新执行'}
          </button>
        </div>
      )}
      <div className="flex-1 overflow-y-auto" key={app?.id}>
        {tab === 'preview' && <PreviewTab hideRunFailureError={showRepairFailureError} />}
        {tab === 'console' && <ConsoleTab />}
        {tab === 'step' && <StepTab />}
      </div>
      {app && (
        <RunHistoryDrawer
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          appId={app.id}
          appName={app.name}
          currentRunId={runId}
          onSelectRun={(run) => {
            if (isRestorableRunStatus(run.status) && app) {
              resume(app, run);
            } else {
              replay(run);
            }
            setTab('preview');
            setHistoryOpen(false);
          }}
        />
      )}
      {app && runId && (
        <RepairInputsDialog
          open={inputDialogOpen}
          app={app}
          runId={runId}
          initialInputs={repairInputs}
          onClose={() => setInputDialogOpen(false)}
          onSave={(inputs) => {
            setRepairInputs(inputs);
            setInputDialogOpen(false);
          }}
        />
      )}
    </div>
  );
}

function FailedRunRepairBar({
  nodeTitle,
  nodeExists,
  hasInputOverrides,
  repairing,
  onEditNode,
  onEditInputs,
  onRerun,
}: {
  nodeTitle: string;
  nodeExists: boolean;
  hasInputOverrides: boolean;
  repairing: boolean;
  onEditNode(): void;
  onEditInputs(): void;
  onRerun(): void;
}) {
  return (
    <section className="border-b border-red-100 bg-red-50/80 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-xs font-medium text-red-800">
            <span className="rounded-full bg-red-100 px-2 py-0.5 text-[10px] uppercase tracking-wider text-red-700">
              修复运行
            </span>
            <span className="truncate">失败节点：{nodeTitle}</span>
            {hasInputOverrides && (
              <span className="rounded-full border border-red-200 bg-white/70 px-1.5 py-0.5 text-[10px] text-red-700">
                已改输入
              </span>
            )}
          </div>
          {!nodeExists ? (
            <div className="mt-1 text-xs text-red-700">
              当前 App graph 中找不到该节点，请先恢复节点后再重新执行。
            </div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onEditNode}
          disabled={!nodeExists || repairing}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-800 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <EditIcon className="h-3.5 w-3.5" />
          编辑失败节点
        </button>
        <button
          type="button"
          onClick={onEditInputs}
          disabled={repairing}
          className="shrink-0 rounded-full border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-800 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-45"
        >
          修改输入
        </button>
        <button
          type="button"
          onClick={onRerun}
          disabled={!nodeExists || repairing}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-black px-3 py-1.5 text-xs font-medium text-white hover:bg-black/80 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <RefreshIcon className="h-3.5 w-3.5" />
          {repairing ? '启动中' : '从失败前检查点重新执行'}
        </button>
      </div>
    </section>
  );
}

function RepairInputsDialog({
  open,
  app,
  runId,
  initialInputs,
  onClose,
  onSave,
}: {
  open: boolean;
  app: App;
  runId: string;
  initialInputs: LaunchInputs | null;
  onClose(): void;
  onSave(inputs: LaunchInputs): void;
}) {
  const userInputs = useMemo(
    () => app.graph.nodes.filter((node): node is UserInputNode => node.type === 'user_input'),
    [app.graph.nodes],
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [sourceInputs, setSourceInputs] = useState<Run['inputs']>({});

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api.getRun(runId)
      .then((run) => {
        if (cancelled) return;
        const base = initialInputs ?? run.inputs ?? {};
        setSourceInputs(base);
        const nextDrafts: Record<string, string> = {};
        for (const node of userInputs) {
          nextDrafts[node.id] = inputValueText(base[node.id]);
        }
        setDrafts(nextDrafts);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : '输入加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, runId, initialInputs, userInputs]);

  const save = () => {
    const next: LaunchInputs = {};
    for (const node of userInputs) {
      const previous = sourceInputs[node.id];
      const value = drafts[node.id] ?? '';
      if (isRunInputValue(previous)) {
        next[node.id] = { ...previous, value };
      } else {
        next[node.id] = value;
      }
    }
    onSave(next);
  };

  return (
    <AppDialog
      open={open}
      onClose={onClose}
      title="修改检查点后的输入"
      description="只有位于失败节点或其下游的输入节点会采用这些值；检查点之前的输入已被冻结，不会修改旧运行记录。"
      widthClassName="max-w-2xl"
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full px-4 py-2 text-sm text-black/55 hover:bg-black/5 hover:text-black"
          >
            取消
          </button>
          <button
            type="button"
            onClick={save}
            disabled={loading || !!error}
            className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white hover:bg-black/80 disabled:cursor-not-allowed disabled:opacity-45"
          >
            保存输入
          </button>
        </>
      }
    >
      {loading ? (
        <div className="rounded-xl border border-black/10 bg-black/[0.02] px-4 py-8 text-center text-sm text-black/50">
          正在读取来源运行输入…
        </div>
      ) : error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : userInputs.length === 0 ? (
        <div className="rounded-xl border border-black/10 bg-black/[0.02] px-4 py-8 text-center text-sm text-black/45">
          当前工作流没有用户输入节点。
        </div>
      ) : (
        <div className="space-y-3">
          {userInputs.map((node) => {
            const disabled = node.input_schema.kind === 'file';
            const sourceValue = sourceInputs[node.id];
            const attachmentCount = isRunInputValue(sourceValue)
              ? sourceValue.attachments?.length ?? 0
              : 0;
            return (
              <label key={node.id} className="block rounded-xl border border-black/10 bg-white p-3">
                <div className="mb-2 flex items-center justify-between gap-3">
                  <span className="text-sm font-medium text-black/80">{node.title || node.input_schema.label}</span>
                  <span className="shrink-0 rounded-full border border-black/10 bg-black/[0.03] px-2 py-0.5 text-[10px] text-black/45">
                    {node.input_schema.kind}
                  </span>
                </div>
                <textarea
                  value={drafts[node.id] ?? ''}
                  onChange={(event) => setDrafts((current) => ({ ...current, [node.id]: event.target.value }))}
                  disabled={disabled}
                  rows={2}
                  className="w-full resize-none rounded-lg border border-black/10 bg-white px-3 py-2 text-sm leading-5 outline-none focus:border-black/40 disabled:bg-black/[0.03] disabled:text-black/35"
                  placeholder={node.input_schema.placeholder ?? node.input_schema.label}
                />
                {attachmentCount > 0 ? (
                  <div className="mt-2 text-xs text-black/45">
                    将保留来源运行中的 {attachmentCount} 个附件引用。
                  </div>
                ) : null}
                {disabled ? (
                  <div className="mt-2 text-xs text-black/45">
                    文件输入首版不在此处替换，将沿用来源运行的文件引用。
                  </div>
                ) : null}
              </label>
            );
          })}
        </div>
      )}
    </AppDialog>
  );
}

function inputValueText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (isRunInputValue(value) && typeof value.value === 'string') return value.value;
  return '';
}

function isRunInputValue(value: unknown): value is { value: string; attachments?: { id: string; name?: string }[] } {
  return !!value && typeof value === 'object' && !Array.isArray(value) && 'value' in value;
}
