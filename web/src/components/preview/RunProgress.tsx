// RunProgress：运行中的视觉反馈面板。
// 只显示 loading 动画、当前节点和进度；详细思考过程请去控制台 tab 查看。

import { useMemo, useState } from 'react';
import { isCancellableRunStatus, useRunStore } from '../../stores/useRunStore';
import { StopIcon } from '../common/Icons';
import type { Step, WorkflowNode } from '../../types';

const NODE_TYPE_LABEL: Record<WorkflowNode['type'], string> = {
  user_input: '用户输入',
  generate: '生成',
  output: '输出',
  asset: '素材',
  condition: '判断',
};
const EMPTY_NODES: WorkflowNode[] = [];

export function RunProgress() {
  const nodes = useRunStore((s) => s.runGraph?.nodes ?? EMPTY_NODES);
  const steps = useRunStore((s) => s.steps);
  const status = useRunStore((s) => s.status);
  const runId = useRunStore((s) => s.runId);
  const cancel = useRunStore((s) => s.cancel);
  const [stopping, setStopping] = useState(false);
  const canStop = runId !== null && isCancellableRunStatus(status);

  const { done, total, current } = useMemo(() => {
    const total = nodes.length;
    let done = 0;
    let current: WorkflowNode | undefined;
    for (const node of nodes) {
      const step = steps[node.id];
      const s: Step['status'] | undefined = step?.status;
      if (s === 'success' || s === 'failed' || s === 'cancelled' || s === 'interrupted' || s === 'skipped') {
        done += 1;
      } else if (!current && (s === 'running' || s === 'waiting_for_user')) {
        current = node;
      }
    }
    return { done, total, current };
  }, [nodes, steps]);

  const progress = total === 0 ? 0 : done / total;
  const isWaiting = status === 'waiting_for_user';

  return (
    <section className="rounded-2xl border border-black/10 bg-white p-6 shadow-card">
      <header className="flex items-center gap-4">
        <PulseDots waiting={isWaiting} />
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-medium uppercase tracking-wider text-black/50">
            {isWaiting ? '等待补充输入' : '运行中'}
          </div>
          <div className="mt-1 truncate text-base font-semibold text-black/85">
            {current ? current.title || NODE_TYPE_LABEL[current.type] : '准备中…'}
          </div>
          {current ? (
            <div className="mt-0.5 text-xs text-black/45">
              {NODE_TYPE_LABEL[current.type]} · 步骤 {Math.min(done + 1, total)} / {total}
            </div>
          ) : null}
        </div>
        {canStop ? (
          <button
            type="button"
            aria-label="停止运行"
            title="停止运行"
            className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-red-50 text-red-600 transition-transform duration-150 ease-out hover:scale-105 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100"
            onClick={() => {
              setStopping(true);
              void cancel().finally(() => setStopping(false));
            }}
            disabled={stopping}
          >
            <StopIcon className="h-5 w-5" />
          </button>
        ) : null}
      </header>

      <div className="mt-5">
        <div className="h-1.5 overflow-hidden rounded-full bg-black/[0.06]">
          <div
            className="h-full rounded-full bg-black/80 transition-[width] duration-300 ease-out"
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
        <div className="mt-1.5 flex justify-between text-[11px] text-black/45">
          <span>已完成 {done} / {total}</span>
          <span>{Math.round(progress * 100)}%</span>
        </div>
      </div>
    </section>
  );
}

function PulseDots({ waiting }: { waiting: boolean }) {
  const color = waiting ? 'bg-amber-500' : 'bg-black/75';
  return (
    <div className="flex shrink-0 items-end gap-1.5" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={`block h-2.5 w-2.5 rounded-full ${color} animate-bounce`}
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  );
}
