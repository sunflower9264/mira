import { useEffect, useMemo, useState } from 'react';
import { useEditorStore } from '../../stores/useEditorStore';
import { useRunStore } from '../../stores/useRunStore';
import type { App } from '../../types';
import * as api from '../../lib/api';
import { AppLaunchView, type LaunchInputs } from './AppLaunchView';
import { HistoryReplayBanner } from './HistoryReplayBanner';
import { HtmlOutputFrame } from './HtmlOutputFrame';
import { RunArtifactsPanel, useRunArtifacts } from './RunArtifactsPanel';
import { RunProgress } from './RunProgress';
import { WaitingInputPanel } from './WaitingInputPanel';

type RunContentVariant = 'preview' | 'app';
type FailureErrorPlacement = 'top' | 'bottom' | 'hidden';
type Phase = 'idle' | 'running' | 'done';
type ResultTab = 'output' | 'files';

export interface AppRunContentProps {
  app: App;
  variant: RunContentVariant;
  failureErrorPlacement?: FailureErrorPlacement;
  onToolsChange?(disabledToolIds: string[]): void;
}

export function AppRunContent({
  app,
  variant,
  failureErrorPlacement = 'bottom',
  onToolsChange,
}: AppRunContentProps) {
  const flushSave = useEditorStore((s) => s.flushSave);
  const status = useRunStore((s) => s.status);
  const steps = useRunStore((s) => s.steps);
  const error = useRunStore((s) => s.error);
  const startRun = useRunStore((s) => s.start);
  const mode = useRunStore((s) => s.mode);
  const replayRun = useRunStore((s) => s.replayRun);
  const runId = useRunStore((s) => s.runId);
  const runGraph = useRunStore((s) => s.runGraph);
  const resetRun = useRunStore((s) => s.reset);
  const waitingInput = useRunStore((s) => s.waitingInput);
  const continueRun = useRunStore((s) => s.continueRun);

  const displayGraph = runGraph ?? app.graph;
  const outputs = useMemo(() => displayGraph.nodes.filter((n) => n.type === 'output'), [displayGraph.nodes]);
  const [resultTab, setResultTab] = useState<ResultTab>('output');
  const isAppView = variant === 'app';
  const isWaiting = status === 'waiting_for_user' && waitingInput !== null;
  const isInterrupted = status === 'interrupted';

  // phase 派生自 store：避免切 tab / 路由进出 / 重 mount 后丢失运行状态。
  const phase: Phase =
    mode === 'replay'
      ? 'done'
      : runId === null
      ? 'idle'
      : status === 'success' || status === 'failed' || status === 'cancelled' || status === 'interrupted'
      ? 'done'
      : 'running';

  const onStart = async (inputs: LaunchInputs) => {
    // 启动失败由 store 的 status='failed' + error 派生至 phase=done 的运行失败红框。
    if (!app.can_run) throw new Error('应用已下架，不能继续运行');
    await flushSave();
    await startRun(app, inputs);
  };

  const restartFromCurrentRun = async () => {
    if (!runId) {
      resetRun();
      return;
    }
    const previous = await api.getRun(runId);
    resetRun();
    await onStart(previous.inputs as LaunchInputs);
  };

  // 至少一个 output 节点已经处于终态（产出 HTML），就把它显示出来。
  const anyOutputReady = outputs.some((o) => {
    const s = steps[o.id]?.status;
    return s === 'success' || s === 'failed' || s === 'skipped';
  });
  const resultReady = (anyOutputReady || phase === 'done') && !isWaiting && !isInterrupted;
  const artifactsState = useRunArtifacts(resultReady ? runId : null, status);
  const hasArtifacts = artifactsState.artifacts.length > 0;
  const resultTabs = useMemo(() => {
    const tabs: { id: ResultTab; label: string }[] = [];
    if (outputs.length) tabs.push({ id: 'output', label: '输出' });
    if (hasArtifacts) tabs.push({ id: 'files', label: '文件' });
    return tabs;
  }, [hasArtifacts, outputs.length]);
  useEffect(() => {
    const defaultTab: ResultTab = outputs.length ? 'output' : 'files';
    setResultTab(defaultTab);
  }, [runId, outputs.length]);
  useEffect(() => {
    const defaultTab: ResultTab = outputs.length ? 'output' : 'files';
    if (!resultTabs.some((tab) => tab.id === resultTab)) {
      setResultTab(defaultTab);
    }
  }, [outputs.length, resultTab, resultTabs]);
  // 输出就绪即进入沉浸布局；未就绪且仍在运行则显示进度面板。
  const showFullOutput = resultReady;
  const showProgress = !showFullOutput && !isWaiting && !isInterrupted && phase === 'running';

  const renderWaiting = isAppView ? (
    <div className="mx-auto flex w-full min-h-0 max-w-3xl flex-1 flex-col">
      <WaitingInputPanel />
    </div>
  ) : (
    <WaitingInputPanel />
  );

  const renderProgress = isAppView ? (
    <div className="mx-auto w-full max-w-3xl">
      <RunProgress />
    </div>
  ) : (
    <RunProgress />
  );

  const failureError = status === 'failed' && error ? (
    <RunFailureError
      error={error}
      className={isAppView ? 'mx-auto w-full max-w-3xl' : ''}
    />
  ) : null;

  const content = (
    <>
      {phase === 'idle' && (
        <AppLaunchView
          app={app}
          onStart={onStart}
          onToolsChange={onToolsChange}
          density={isAppView ? 'spacious' : 'compact'}
        />
      )}

      {(phase === 'running' || phase === 'done') && (
        <div className={isAppView ? `flex flex-col gap-5 px-6 py-10 ${isWaiting ? 'min-h-0 flex-1' : 'min-h-full'}` : 'flex min-h-0 flex-1 flex-col gap-5 p-5'}>
          {mode === 'replay' && replayRun && (
            <HistoryReplayBanner
              run={replayRun}
              onExit={() => {
                resetRun();
              }}
            />
          )}

          {isAppView && !showFullOutput && !isWaiting && (
            <h2 className="text-3xl font-semibold">{app.name}</h2>
          )}

          {failureErrorPlacement === 'top' ? failureError : null}

          {/* 等待用户输入 → 卡片化交互；其它运行中状态 → 进度面板 */}
          {isWaiting ? renderWaiting : showProgress ? renderProgress : null}

          {isInterrupted && (
            <RecoveryPanel
              isAppView={isAppView}
              onContinue={() => void continueRun()}
              onRestart={() => void restartFromCurrentRun()}
            />
          )}

          {/* 结果区：output / 文件产物 tab */}
          {showFullOutput && (
            <div className="flex flex-1 flex-col gap-4">
              <ResultTabs tabs={resultTabs} value={resultTab} onChange={setResultTab} />
              {resultTab === 'output' && (
                <>
                  {outputs.map((o) => {
                    if (o.type !== 'output') return null;
                    const outputStep = steps[o.id];
                    const outputDone =
                      outputStep?.status === 'success' ||
                      outputStep?.status === 'failed' ||
                      outputStep?.status === 'skipped';
                    const text = outputDone ? formatOutputValue(outputStep?.output) : '';
                    return (
                      <div key={o.id} className="flex flex-1 flex-col overflow-hidden rounded-2xl border border-black/10 bg-white shadow-card">
                        {text ? (
                          <HtmlOutputFrame
                            html={text}
                            artifacts={artifactsState.artifacts}
                            title={o.title || '输出预览'}
                            className="block w-full rounded-2xl border-0 bg-white"
                          />
                        ) : outputStep?.status === 'skipped' ? (
                          <div className="p-6 text-xs text-black/45">已跳过</div>
                        ) : (
                          <div className="p-6 text-xs text-black/45">尚未产出内容</div>
                        )}
                        {outputStep?.status === 'failed' && outputStep.error && (
                          <div className="border-t border-red-100 bg-red-50 px-4 py-2 text-xs text-red-700">
                            {outputStep.error}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {outputs.length === 0 ? (
                    <div className="rounded-2xl border border-black/10 bg-white p-6 text-sm text-black/45 shadow-card">这个应用没有输出节点。</div>
                  ) : null}
                </>
              )}
              {resultTab === 'files' && (
                runId ? <RunArtifactsPanel runId={runId} state={artifactsState} /> : <div className="rounded-2xl border border-black/10 bg-white p-6 text-sm text-black/45 shadow-card">暂无运行记录。</div>
              )}
            </div>
          )}

          {phase === 'done' && runId && !showFullOutput ? (
            <RunArtifactsPanel
              runId={runId}
              className={isAppView && !showFullOutput ? 'mx-auto w-full max-w-3xl' : ''}
            />
          ) : null}

          {failureErrorPlacement === 'bottom' ? failureError : null}
        </div>
      )}
    </>
  );

  if (isAppView) {
    return (
      <div className={`absolute inset-0 ${isWaiting ? 'flex flex-col' : 'overflow-y-auto'}`}>
        {content}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col bg-[#F4F5F7] text-[#0B0B0F]">
      {content}
    </div>
  );
}

export function RunFailureError({ error, className = '' }: { error: string; className?: string }) {
  const classes = ['rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700', className]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes}>
      <div className="mb-1 font-semibold">运行失败</div>
      <div className="whitespace-pre-wrap break-words">{error}</div>
    </div>
  );
}

function ResultTabs({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: ResultTab; label: string }[];
  value: ResultTab;
  onChange(value: ResultTab): void;
}) {
  return (
    <div className="inline-flex w-fit rounded-full border border-black/10 bg-white p-1 shadow-card">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          onClick={() => onChange(tab.id)}
          className={`h-8 rounded-full px-3 text-sm transition ${
            value === tab.id ? 'bg-black text-white' : 'text-black/55 hover:bg-black/[0.04] hover:text-black'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

function RecoveryPanel({
  isAppView,
  onContinue,
  onRestart,
}: {
  isAppView: boolean;
  onContinue(): void;
  onRestart(): void;
}) {
  return (
    <section className={isAppView ? 'mx-auto w-full max-w-3xl rounded-2xl border border-amber-200 bg-amber-50 p-5 text-amber-900' : 'rounded-2xl border border-amber-200 bg-amber-50 p-5 text-amber-900'}>
      <div className="text-[11px] font-medium uppercase tracking-wider text-amber-700">运行已中断</div>
      <h3 className="mt-1 text-base font-semibold">可以从未完成节点继续</h3>
      <p className="mt-2 text-sm text-amber-800">
        继续运行会跳过已完成节点；当前中断节点会尽量恢复同一 Agent 会话，部分工具动作可能需要重新确认。
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onContinue}
          className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white hover:bg-black/80"
        >
          继续运行
        </button>
        <button
          type="button"
          onClick={onRestart}
          className="rounded-full border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-900 hover:bg-amber-100"
        >
          重新运行
        </button>
      </div>
    </section>
  );
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
