import { useEffect, useRef, useState } from 'react';
import { Bot, ChevronDown, ExternalLink, Square } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import type { AiActivity } from '../../types';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { useAiActivityStore } from '../../stores/useAiActivityStore';
import { AiActivityIndicator } from './AiActivityIndicator';

const KIND_LABEL: Record<AiActivity['kind'], string> = {
  app_run: '工作流',
  workspace_turn: '工作空间',
  nl_compile: '画布生成',
  prompt_assistant: '提示词助手',
  wiki: 'Wiki',
};

export function AiActivityDock({ compact = false }: { compact?: boolean }) {
  const activities = useAiActivityStore((state) => state.activities);
  const refresh = useAiActivityStore((state) => state.refresh);
  const stop = useAiActivityStore((state) => state.stop);
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [stoppingId, setStoppingId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    void refresh().catch(() => undefined);
    const poll = window.setInterval(() => void refresh().catch(() => undefined), 3000);
    const onFocus = () => void refresh().catch(() => undefined);
    window.addEventListener('focus', onFocus);
    return () => {
      window.clearInterval(poll);
      window.removeEventListener('focus', onFocus);
    };
  }, [refresh]);

  useEffect(() => {
    if (!activities.length) {
      setOpen(false);
      return;
    }
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activities.length]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  if (!activities.length) return null;
  const waitingCount = activities.filter((item) => item.status === 'waiting_user').length;
  const headline = waitingCount ? `等待你回复 · ${waitingCount}` : `AI 处理中 · ${activities.length}`;
  const headlineStatus = waitingCount ? 'waiting_user' : activities.some((item) => item.status === 'working') ? 'working' : 'queued';

  return (
    <div ref={rootRef} className={compact ? 'relative z-[70] shrink-0' : 'fixed right-3 top-[calc(4rem+env(safe-area-inset-top))] z-[70] sm:right-5 sm:top-20'}>
      {open ? (
        <div className={`w-[min(22rem,calc(100vw-1.5rem))] overflow-hidden rounded-2xl border border-black/10 bg-white shadow-[0_18px_60px_rgba(0,0,0,0.16)] ${compact ? 'absolute -right-12 top-full mt-2' : 'mb-2'}`}>
          <div className="flex items-center justify-between border-b border-black/[0.06] px-4 py-3">
            <div className="text-sm font-semibold text-black/80">AI 任务</div>
            <div className="text-[11px] text-black/40">{activities.length} 个进行中</div>
          </div>
          <div className="max-h-[min(25rem,60vh)] divide-y divide-black/[0.06] overflow-y-auto">
            {activities.map((activity) => (
              <div key={`${activity.kind}:${activity.id}`} className="px-4 py-3">
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-black/75">{activity.title}</span>
                      <span className="shrink-0 rounded-full bg-black/[0.04] px-2 py-0.5 text-[10px] text-black/40">{KIND_LABEL[activity.kind]}</span>
                    </div>
                    <div className={`mt-1.5 text-xs ${activity.status === 'waiting_user' ? 'text-amber-700' : 'text-black/45'}`}>
                      <AiActivityIndicator status={activity.status} label={activity.phase} compact />
                    </div>
                    {activity.started_at ? <div className="mt-1 text-[10px] tabular-nums text-black/30" aria-hidden="true">已用时 {formatElapsed(activity.started_at, now)}</div> : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {activity.cancellable ? (
                      <button
                        type="button"
                        disabled={stoppingId === activity.id}
                        onClick={() => {
                          setStoppingId(activity.id);
                          void stop(activity)
                            .catch((error) => showCaughtError(error, '停止 AI 任务失败', '停止失败'))
                            .finally(() => setStoppingId(null));
                        }}
                        className="grid h-8 w-8 place-items-center rounded-full text-red-500 hover:bg-red-50 disabled:opacity-35"
                        aria-label={`停止 ${activity.title}`}
                      >
                        <Square className="h-3 w-3 fill-current" />
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => { setOpen(false); navigate(activity.href); }}
                      className="grid h-8 w-8 place-items-center rounded-full text-black/40 hover:bg-black/5 hover:text-black/70"
                      aria-label={`返回 ${activity.title}`}
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={headline}
        className={`${compact ? 'relative grid h-11 w-11 place-items-center rounded-full' : 'ml-auto flex h-10 items-center gap-2 rounded-full border px-3.5 text-xs font-medium shadow-pill transition hover:-translate-y-0.5'} ${waitingCount ? 'border-amber-200 bg-amber-50 text-amber-900' : 'border-black/10 bg-white text-black/70'}`}
      >
        {compact ? <><Bot className="h-5 w-5" /><span className={`absolute right-2 top-2 h-1.5 w-1.5 rounded-full ${waitingCount ? 'bg-amber-500' : 'bg-emerald-500'}`} /></> : <>
        <AiActivityIndicator status={headlineStatus} label={headline} compact />
        <ChevronDown className={`h-3.5 w-3.5 transition ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
        </>}
      </button>
    </div>
  );
}

function formatElapsed(startedAt: string, now: number): string {
  const elapsedSeconds = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000));
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return minutes > 0 ? `${minutes}分${String(seconds).padStart(2, '0')}秒` : `${seconds}秒`;
}
