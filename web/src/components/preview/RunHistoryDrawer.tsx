import { useEffect, useMemo, useRef, useState } from 'react';
import { Drawer } from '../common/Drawer';
import { EditIcon, TrashIcon } from '../common/Icons';
import * as api from '../../lib/api';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import type { Run, RunSummary } from '../../types';

interface RunHistoryDrawerProps {
  open: boolean;
  onClose(): void;
  appId: string;
  appName: string;
  currentRunId?: string | null;
  onSelectRun(run: Run): void;
}

const DISPLAY_NAME_LIMIT = 24;

const STATUS_LABEL: Record<Run['status'], string> = {
  pending: '排队中',
  running: '运行中',
  waiting_for_user: '等待用户',
  interrupted: '已中断',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
};

const STATUS_TONE: Record<Run['status'], string> = {
  pending: 'border-black/10 bg-black/[0.04] text-black/55',
  running: 'border-blue-200 bg-blue-50 text-blue-700',
  waiting_for_user: 'border-amber-200 bg-amber-50 text-amber-700',
  interrupted: 'border-amber-200 bg-amber-50 text-amber-700',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  failed: 'border-red-200 bg-red-50 text-red-700',
  cancelled: 'border-black/10 bg-black/[0.04] text-black/55',
};

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diff = Date.now() - then;
  const sec = Math.round(diff / 1000);
  if (sec < 60) return '刚刚';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function compactText(value: string): string {
  return value.trim().replace(/\s+/g, ' ');
}

function truncateDisplayName(name: string): string {
  return name.length > DISPLAY_NAME_LIMIT ? name.slice(0, DISPLAY_NAME_LIMIT) + '...' : name;
}

function formatFallbackTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function inputText(value: unknown): string {
  if (typeof value === 'string') return compactText(value);
  if (!value || typeof value !== 'object') return '';
  const item = value as { value?: unknown; attachments?: unknown };
  if (typeof item.value === 'string' && compactText(item.value)) {
    return compactText(item.value);
  }
  if (Array.isArray(item.attachments)) {
    for (const attachment of item.attachments) {
      if (attachment && typeof attachment === 'object') {
        const name = (attachment as { name?: unknown }).name;
        if (typeof name === 'string' && compactText(name)) return compactText(name);
      }
    }
  }
  return '';
}

function summarizeInputs(inputs: RunSummary['inputs']): string {
  const values = Object.values(inputs ?? {});
  for (const value of values) {
    const text = inputText(value);
    if (text) return text;
  }
  return '';
}

function runDisplayName(run: RunSummary, appName: string): string {
  const savedName = typeof run.name === 'string' ? compactText(run.name) : '';
  if (savedName) return savedName;
  const inputName = summarizeInputs(run.inputs);
  if (inputName) return inputName;
  const time = formatFallbackTime(run.started_at);
  return time ? `${appName} · ${time}` : appName;
}

export function RunHistoryDrawer({
  open,
  onClose,
  appId,
  appName,
  currentRunId,
  onSelectRun,
}: RunHistoryDrawerProps) {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectingId, setSelectingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState('');
  const [savingId, setSavingId] = useState<string | null>(null);
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    api
      .listRunSummaries(appId)
      .then((data) => {
        if (!cancelled) setRuns(data);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, appId]);

  useEffect(() => {
    if (!open) {
      setConfirmId(null);
      setEditingId(null);
      setSelectingId(null);
      if (confirmTimer.current) clearTimeout(confirmTimer.current);
    }
  }, [open]);

  const armConfirm = (id: string) => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    setEditingId(null);
    setConfirmId(id);
    confirmTimer.current = setTimeout(() => setConfirmId(null), 3000);
  };

  const startEditing = (run: RunSummary) => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    setConfirmId(null);
    setEditingId(run.id);
    setDraftName(runDisplayName(run, appName));
  };

  const submitRename = async (id: string) => {
    const nextName = compactText(draftName);
    if (!nextName) return;
    setSavingId(id);
    try {
      const updated = await api.patchRun(id, { name: nextName });
      setRuns((current) => (current ? current.map((run) => (run.id === id ? {
        ...run,
        name: updated.name,
        status: updated.status,
        inputs: updated.inputs,
        started_at: updated.started_at,
        finished_at: updated.finished_at,
        error: updated.error,
      } : run)) : current));
      setEditingId(null);
    } finally {
      setSavingId(null);
    }
  };

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

  const handleDelete = async (id: string) => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    setConfirmId(null);
    setDeletingId(id);
    try {
      await api.deleteRun(id);
      setRuns((current) => (current ? current.filter((run) => run.id !== id) : current));
    } finally {
      setDeletingId(null);
    }
  };

  const titleNode = useMemo(() => {
    const count = runs?.length ?? 0;
    return (
      <div className="flex items-center gap-2">
        <span>历史记录</span>
        {!loading && runs && (
          <span className="rounded-full bg-black/5 px-2 py-0.5 text-[11px] text-black/55">
            {count}
          </span>
        )}
      </div>
    );
  }, [loading, runs]);

  return (
    <Drawer open={open} onClose={onClose} title={titleNode} side="left">
      {loading && !runs && (
        <div className="px-4 py-6 text-sm text-black/45">加载中…</div>
      )}
      {!loading && runs && runs.length === 0 && (
        <div className="flex flex-col items-center px-6 py-12 text-center">
          <div className="text-sm text-black/55">暂无运行记录</div>
          <div className="mt-1 text-xs text-black/40">点击「开始」运行后会出现在这里</div>
        </div>
      )}
      {runs && runs.length > 0 && (
        <ul className="px-2 py-2">
          {runs.map((run) => {
            const active = run.id === currentRunId;
            const confirming = confirmId === run.id;
            const editing = editingId === run.id;
            const deleting = deletingId === run.id;
            const saving = savingId === run.id;
            const selecting = selectingId === run.id;
            const name = runDisplayName(run, appName);
            const displayName = truncateDisplayName(name);
            return (
              <li
                key={run.id}
                className={`group relative mb-1 rounded-xl border transition ${
                  active
                    ? 'border-black/40 bg-black/[0.04]'
                    : 'border-transparent hover:border-black/10 hover:bg-black/[0.02]'
                }`}
                  >
                {editing ? (
                  <div className="px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs text-black/55">{formatRelative(run.started_at)}</span>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${STATUS_TONE[run.status]}`}>
                        {STATUS_LABEL[run.status]}
                      </span>
                    </div>
                    <input
                      autoFocus
                      value={draftName}
                      maxLength={80}
                      onChange={(event) => setDraftName(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') void submitRename(run.id);
                        if (event.key === 'Escape') setEditingId(null);
                      }}
                      className="mt-2 w-full rounded-lg border border-black/10 px-2.5 py-1.5 text-sm outline-none focus:border-black/40"
                    />
                    <div className="mt-2 flex justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => setEditingId(null)}
                        className="rounded-full px-2.5 py-1 text-[11px] text-black/55 hover:bg-black/5 hover:text-black"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        disabled={saving || compactText(draftName).length === 0}
                        onClick={() => void submitRename(run.id)}
                        className="rounded-full bg-black px-2.5 py-1 text-[11px] text-white hover:bg-black/80 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        保存
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    type="button"
                    disabled={selectingId !== null}
                    onClick={() => void selectRun(run.id)}
                    className="block w-full px-3 py-2.5 text-left"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs text-black/55">
                        {formatRelative(run.started_at)}
                      </span>
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${STATUS_TONE[run.status]}`}
                      >
                        {STATUS_LABEL[run.status]}
                      </span>
                    </div>
                    <div className="mt-1 truncate pr-14 text-sm text-black/80" title={name}>
                      {selecting ? '加载中...' : displayName}
                    </div>
                  </button>
                )}
                {!editing && (
                  confirming ? (
                    <div className="absolute bottom-1.5 right-2 flex items-center gap-1">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          if (confirmTimer.current) clearTimeout(confirmTimer.current);
                          setConfirmId(null);
                        }}
                        className="rounded-full bg-white px-2.5 py-1 text-[11px] text-black/55 shadow-sm hover:text-black"
                      >
                        取消
                      </button>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          void handleDelete(run.id);
                        }}
                        className="rounded-full bg-red-600 px-2.5 py-1 text-[11px] text-white hover:bg-red-700"
                      >
                        删除
                      </button>
                    </div>
                  ) : (
                    <div className="absolute bottom-1.5 right-2 flex items-center gap-1 opacity-0 transition group-hover:opacity-100">
                      <button
                        type="button"
                        aria-label="编辑名称"
                        title="编辑名称"
                        onClick={(event) => {
                          event.stopPropagation();
                          startEditing(run);
                        }}
                        className="rounded-full p-1.5 text-black/35 hover:bg-black/5 hover:text-black"
                      >
                        <EditIcon className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label="删除"
                        title="删除"
                        disabled={deleting}
                        onClick={(event) => {
                          event.stopPropagation();
                          armConfirm(run.id);
                        }}
                        className="rounded-full p-1.5 text-black/35 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                      >
                        <TrashIcon className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Drawer>
  );
}
