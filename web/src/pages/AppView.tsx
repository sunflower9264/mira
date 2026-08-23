// AppView (PRD §7.3). Fullscreen view that runs the workflow.

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useEditorStore } from '../stores/useEditorStore';
import { isCancellableRunStatus, isRestorableRunStatus, useRunStore } from '../stores/useRunStore';
import { ArrowLeftIcon, MenuIcon, RefreshIcon, SettingsIcon } from '../components/common/Icons';
import { UserMenu } from '../components/common/UserMenu';
import { SettingsDialog } from '../components/settings/SettingsDialog';
import { RunHistoryDrawer } from '../components/preview/RunHistoryDrawer';
import { AppMenu } from '../components/common/AppMenu';
import { EditAppDialog } from '../components/common/EditAppDialog';
import { VersionHistoryDialog } from '../components/common/VersionHistoryDialog';
import { PublishButton } from '../components/common/PublishButton';
import { selectIsAdmin, useAuthStore } from '../stores/useAuthStore';
import { AppRunContent } from '../components/preview/AppRunContent';
import { useRunProgress } from '../components/preview/useRunProgress';

export function AppView({ readOnly = false }: { readOnly?: boolean }) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const load = useEditorStore((s) => s.load);
  const loadedApp = useEditorStore((s) => s.app);
  const setMeta = useEditorStore((s) => s.setMeta);
  const flushSave = useEditorStore((s) => s.flushSave);
  const setGraph = useEditorStore((s) => s.setGraph);
  const resumeRun = useRunStore((s) => s.resume);
  const restoreActiveRun = useRunStore((s) => s.restoreActiveRun);
  const resetRun = useRunStore((s) => s.reset);
  const replay = useRunStore((s) => s.replay);
  const runId = useRunStore((s) => s.runId);
  const status = useRunStore((s) => s.status);
  const progress = useRunProgress();

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const isAdmin = useAuthStore(selectIsAdmin);
  const showReset = runId === null || !isCancellableRunStatus(status);
  const app = loadedApp?.id === id ? loadedApp : null;

  useEffect(() => {
    if (id) void load(id);
    return () => resetRun();
  }, [id, load, resetRun]);

  // 刷新 / 重新进入 AppView 时，检查 sessionStorage 中是否有未结束的 run，并续订其事件流。
  // 仅在当前没有活跃 run 时触发，避免覆盖正在进行的运行或回放。
  useEffect(() => {
    if (!app || runId !== null) return;
    void restoreActiveRun(app);
  }, [app, runId, restoreActiveRun]);

  if (!app) return <div className="p-10 text-black/55">加载中…</div>;
  const readonly = readOnly || !app.can_edit;

  return (
    <div className="flex h-full flex-col bg-[#F4F5F7] text-[#0B0B0F]">
      <header className="h-12 px-3 flex items-center justify-between border-b border-black/5 bg-white">
        <div className="flex items-center gap-3">
          <button className="p-1.5 rounded-full hover:bg-black/5" onClick={() => navigate(readonly ? '/' : `/apps/${app.id}/editor`)} aria-label="返回">
            <ArrowLeftIcon className="w-4 h-4" />
          </button>
          <span className="text-sm font-medium">{app.name}</span>
          <span className="text-[14px] uppercase tracking-wider text-black/45">
            {app.archived_at ? '已下架' : app.status === 'published' ? app.visibility === 'private' ? '私有发布' : '已发布' : '草稿'}
          </span>
        </div>
        {readonly ? (
          <div className="text-sm text-black/45">应用</div>
        ) : (
          <div className="flex items-center gap-1 text-sm bg-black/5 rounded-full p-1">
            <button
              onClick={() => navigate(`/apps/${app.id}/editor`)}
              className="px-3 py-1 rounded-full text-black/55 hover:text-black"
            >
              编辑器
            </button>
            <span className="px-3 py-1 rounded-full bg-white shadow-sm text-black">应用</span>
          </div>
        )}
        <div className="flex items-center gap-3 text-sm text-black/55">
          {!readonly && <span className="text-xs">已保存</span>}
          {!readonly && <PublishButton app={app} />}
          {!readonly && <AppMenu onEdit={() => setEditOpen(true)} onHistory={() => setVersionsOpen(true)} />}
          {!readonly && isAdmin && (
            <button className="p-1.5 rounded-full hover:bg-black/5" aria-label="设置" onClick={() => setSettingsOpen(true)}><SettingsIcon className="w-4 h-4" /></button>
          )}
          <UserMenu iconClassName="w-4 h-4" />
        </div>
      </header>
      {!readonly && isAdmin && <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />}
      {!readonly && (
        <>
          <EditAppDialog
            open={editOpen}
            onClose={() => setEditOpen(false)}
            initialName={app.name}
            initialDescription={app.description}
            initialCover={app.cover}
            appId={app.id}
            onSave={async ({ name, description, cover }) => {
              setMeta({ name, description, cover });
              await flushSave();
            }}
          />
          <VersionHistoryDialog
            open={versionsOpen}
            onClose={() => setVersionsOpen(false)}
            appId={app.id}
            onCloned={(newAppId) => {
              setVersionsOpen(false);
              navigate(`/apps/${newAppId}/editor`);
            }}
          />
        </>
      )}
      <div className="h-1 bg-black/5 relative">
        <div className="absolute inset-y-0 left-0 bg-black/55 transition-[width] duration-200" style={{ width: `${progress * 100}%` }} />
      </div>
      <div className="flex-1 relative overflow-hidden">
        <AppRunContent
          app={app}
          variant="app"
          failureErrorPlacement="top"
          onToolsChange={readonly ? undefined : (disabledToolIds) => {
            setGraph({
              ...app.graph,
              tools: { disabled_tool_ids: disabledToolIds },
            });
          }}
        />
        <button
          className="absolute top-3 left-3 z-10 p-1.5 rounded-full hover:bg-black/10"
          aria-label="历史记录"
          onClick={() => setHistoryOpen(true)}
        >
          <MenuIcon className="w-4 h-4" />
        </button>
        {showReset ? (
          <button
            className="absolute top-3 right-3 z-10 p-1.5 rounded-full text-black/55 hover:bg-black/10"
            aria-label="重新开始"
            title="重新开始"
            onClick={() => resetRun()}
          >
            <RefreshIcon className="w-4 h-4" />
          </button>
        ) : null}
        <RunHistoryDrawer
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          appId={app.id}
          appName={app.name}
          currentRunId={runId}
          onSelectRun={(run) => {
            if (isRestorableRunStatus(run.status)) {
              resumeRun(app, run);
            } else {
              replay(run);
            }
            setHistoryOpen(false);
          }}
        />
      </div>
    </div>
  );
}
