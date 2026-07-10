// Editor page (PRD §7.2). Split-pane: left canvas, right preview/console/step.

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useEditorStore } from '../stores/useEditorStore';
import { useRunStore } from '../stores/useRunStore';
import { Canvas } from '../components/editor/Canvas';
import { PreviewPanel } from '../components/preview/PreviewPanel';
import { ArrowLeftIcon, SettingsIcon } from '../components/common/Icons';
import { UserMenu } from '../components/common/UserMenu';
import { useSplitPane } from '../hooks/useSplitPane';
import { SettingsDialog } from '../components/settings/SettingsDialog';
import { AppMenu } from '../components/common/AppMenu';
import { EditAppDialog } from '../components/common/EditAppDialog';
import { VersionHistoryDialog } from '../components/common/VersionHistoryDialog';
import { PublishButton } from '../components/common/PublishButton';
import { selectIsAdmin, useAuthStore } from '../stores/useAuthStore';

export function Editor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const load = useEditorStore((s) => s.load);
  const loadedApp = useEditorStore((s) => s.app);
  const rename = useEditorStore((s) => s.rename);
  const setMeta = useEditorStore((s) => s.setMeta);
  const flushSave = useEditorStore((s) => s.flushSave);
  const saveStatus = useEditorStore((s) => s.saveStatus);
  const saveError = useEditorStore((s) => s.saveError);
  const resetRun = useRunStore((s) => s.reset);
  const restoreActiveRun = useRunStore((s) => s.restoreActiveRun);
  const runId = useRunStore((s) => s.runId);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const isAdmin = useAuthStore(selectIsAdmin);
  const app = loadedApp?.id === id ? loadedApp : null;

  const split = useSplitPane({ initial: 0.7, min: 480 });

  useEffect(() => {
    if (id) void load(id);
    return () => resetRun();
  }, [id, load, resetRun]);

  useEffect(() => {
    if (app && !app.can_edit) {
      navigate(`/market/apps/${app.id}`, { replace: true });
    }
  }, [app, navigate]);

  useEffect(() => {
    if (!app || runId !== null) return;
    void restoreActiveRun(app);
  }, [app, runId, restoreActiveRun]);

  // Keyboard: Ctrl/Cmd+Z for undo, Ctrl/Cmd+Y or Ctrl/Cmd+Shift+Z for redo.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      // ignore if focused element is editable
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || (e.target as HTMLElement | null)?.isContentEditable) return;
      if (e.key.toLowerCase() === 'z' && !e.shiftKey) {
        useEditorStore.getState().undo();
        e.preventDefault();
      } else if (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey)) {
        useEditorStore.getState().redo();
        e.preventDefault();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  if (!app) return <div className="p-10 text-black/55">加载中…</div>;

  return (
    <div className="h-full flex flex-col" ref={split.containerRef}>
      <header className="h-12 px-3 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center border-b border-black/5 bg-white">
        <div className="flex min-w-0 items-center gap-3">
          <button className="p-1.5 rounded-full hover:bg-black/5" onClick={() => navigate('/')} aria-label="返回">
            <ArrowLeftIcon className="w-4 h-4" />
          </button>
          <input
            value={app.name}
            onChange={(e) => rename(e.target.value)}
            className="text-sm font-medium bg-transparent outline-none w-[260px]"
          />
          <span className="text-[14px] uppercase tracking-wider text-black/45">
            {app.status === 'published' ? '已发布' : '草稿'}
          </span>
        </div>
        <div className="flex items-center gap-1 text-sm bg-black/5 rounded-full p-1 justify-self-center">
          <span className="px-3 py-1 rounded-full bg-white shadow-sm">编辑器</span>
          <button
            onClick={() => navigate(`/apps/${app.id}/view`)}
            className="px-3 py-1 rounded-full text-black/55 hover:text-black"
          >
            应用
          </button>
        </div>
        <div className="flex items-center gap-3 text-sm text-black/55 justify-self-end">
          <span className="w-16 text-right text-xs" title={saveError ?? undefined}>
            {saveStatus === 'saving'
              ? '保存中…'
              : saveStatus === 'error'
                ? '保存失败'
                : saveStatus === 'dirty'
                  ? '待保存'
                  : '已保存'}
          </span>
          <PublishButton app={app} />
          <AppMenu onEdit={() => setEditOpen(true)} onHistory={() => setHistoryOpen(true)} />
          {isAdmin && (
            <button className="p-1.5 rounded-full hover:bg-black/5" aria-label="设置" onClick={() => setSettingsOpen(true)}><SettingsIcon className="w-4 h-4" /></button>
          )}
          <UserMenu iconClassName="w-4 h-4" />
        </div>
      </header>
      {isAdmin && <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />}
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
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        appId={app.id}
        onCloned={(newAppId) => {
          setHistoryOpen(false);
          navigate(`/apps/${newAppId}/editor`);
        }}
      />
      <div className="flex-1 relative flex overflow-hidden">
        <div className="relative" style={{ width: split.leftPct + '%' }}>
          <Canvas />
        </div>
        <div
          onPointerDown={split.onPointerDown}
          className="relative w-1 cursor-col-resize touch-none bg-transparent transition-colors before:absolute before:inset-y-0 before:left-1/2 before:w-4 before:-translate-x-1/2 hover:bg-black/10"
          aria-label="调整宽度"
        />
        <div className="flex-1 min-w-0 bg-white border-l border-black/5 shadow-[-12px_0_24px_rgba(15,23,42,0.08)]">
          <PreviewPanel />
        </div>
      </div>
    </div>
  );
}
