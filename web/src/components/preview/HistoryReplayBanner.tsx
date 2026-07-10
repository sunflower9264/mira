import type { Run } from '../../types';

const STATUS_LABEL: Record<Run['status'], string> = {
  pending: '排队中',
  running: '运行中',
  waiting_for_user: '等待用户',
  interrupted: '已中断',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
};

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

interface HistoryReplayBannerProps {
  run: Run;
  onExit(): void;
}

export function HistoryReplayBanner({ run, onExit }: HistoryReplayBannerProps) {
  return (
    <div className="mx-auto mb-4 flex max-w-3xl flex-wrap items-center justify-between gap-2 rounded-2xl border border-amber-200 bg-amber-50/80 px-4 py-2 text-sm text-amber-800">
      <div className="flex items-center gap-2">
        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wider">
          历史
        </span>
        <span className="text-amber-900">查看历史运行</span>
        <span className="text-amber-700/80">·</span>
        <span className="text-amber-700">{formatTime(run.started_at)}</span>
        <span className="text-amber-700/80">·</span>
        <span className="text-amber-700">{STATUS_LABEL[run.status]}</span>
      </div>
      <button
        type="button"
        onClick={onExit}
        className="rounded-full border border-amber-300 bg-white/80 px-3 py-1 text-xs font-medium text-amber-800 transition hover:bg-white"
      >
        返回当前
      </button>
    </div>
  );
}
