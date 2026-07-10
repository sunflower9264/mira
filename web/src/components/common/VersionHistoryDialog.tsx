// 历史版本弹窗：手动创建快照、克隆为新应用。

import { useEffect, useState } from 'react';
import { AppDialog } from './AppDialog';
import { ConfirmDialog } from './ConfirmDialog';
import * as api from '../../lib/api';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import type { AppVersion } from '../../types';

interface VersionHistoryDialogProps {
  open: boolean;
  onClose(): void;
  appId: string;
  onCloned(newAppId: string): void;
}

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

export function VersionHistoryDialog({
  open,
  onClose,
  appId,
  onCloned,
}: VersionHistoryDialogProps) {
  const [versions, setVersions] = useState<AppVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [label, setLabel] = useState('');
  const [pendingClone, setPendingClone] = useState<AppVersion | null>(null);
  const [cloning, setCloning] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLabel('');
    setPendingClone(null);
    setLoading(true);
    let cancelled = false;
    api
      .listVersions(appId)
      .then((list) => {
        if (cancelled) return;
        setVersions(list);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        showCaughtError(e, '加载失败', '加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, appId]);

  const handleCreate = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const next = await api.createVersion(appId, label);
      setVersions((prev) => [next, ...prev]);
      setLabel('');
    } catch (e) {
      showCaughtError(e, '创建失败', '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const handleClone = async () => {
    if (!pendingClone || cloning) return;
    setCloning(true);
    try {
      const cloned = await api.cloneFromVersion(pendingClone.id);
      setPendingClone(null);
      onCloned(cloned.id);
    } catch (e) {
      showCaughtError(e, '克隆失败', '克隆失败');
    } finally {
      setCloning(false);
    }
  };

  return (
    <>
      <AppDialog
        open={open}
        onClose={onClose}
        title="历史版本"
        description="手动记录当前应用的快照。点击「克隆为新应用」可基于该版本创建副本。"
        widthClassName="max-w-2xl"
      >
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !creating) {
                  e.preventDefault();
                  void handleCreate();
                }
              }}
              placeholder="备注（可选）"
              className="flex-1 rounded-xl border border-black/10 px-3 py-2 text-sm outline-none transition focus:border-black/30"
            />
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={creating}
              className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {creating ? '创建中…' : '创建快照'}
            </button>
          </div>

          <div className="max-h-[50vh] overflow-y-auto rounded-2xl border border-black/5 bg-black/[0.02]">
            {loading ? (
              <div className="px-4 py-10 text-center text-sm text-black/45">加载中…</div>
            ) : versions.length === 0 ? (
              <div className="px-4 py-10 text-center text-sm text-black/45">
                还没有快照，点上方按钮记录当前版本。
              </div>
            ) : (
              <ul className="divide-y divide-black/5">
                {versions.map((version, idx) => {
                  const ordinal = versions.length - idx;
                  return (
                    <li
                      key={version.id}
                      className="flex items-center gap-3 px-4 py-3"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="rounded-full border border-black/10 bg-white px-1.5 py-0.5 text-[10px] text-black/55">
                            v{ordinal}
                          </span>
                          <span className="truncate text-sm font-medium text-black/85">
                            {version.name}
                          </span>
                          {version.label ? (
                            <span className="truncate rounded-full border border-black/10 bg-white px-1.5 py-0.5 text-[10px] text-black/60">
                              {version.label}
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-1 text-[11px] text-black/45">
                          {formatRelative(version.created_at)} · {version.graph.nodes.length} 个节点
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => setPendingClone(version)}
                        className="shrink-0 rounded-full border border-black/10 px-3 py-1.5 text-xs font-medium text-black transition hover:bg-black/5"
                      >
                        克隆为新应用
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </AppDialog>

      <ConfirmDialog
        open={!!pendingClone}
        onClose={() => {
          if (!cloning) setPendingClone(null);
        }}
        onConfirm={handleClone}
        title="克隆为新应用"
        description={
          pendingClone
            ? `将基于此快照创建一个新的草稿应用，原应用不受影响。`
            : ''
        }
        confirmLabel={cloning ? '创建中…' : '确认克隆'}
        busy={cloning}
      />
    </>
  );
}
