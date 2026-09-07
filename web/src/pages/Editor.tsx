// Conversation-led workflow editor with a shared canvas and run workspace.

import { useEffect, useState } from 'react';
import { GitBranch, ListOrdered, Maximize2, MessageSquare, Minimize2, Play, Terminal } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { useEditorStore } from '../stores/useEditorStore';
import { useRunStore } from '../stores/useRunStore';
import { Canvas } from '../components/editor/Canvas';
import { PreviewPanel, type PreviewPanelTab } from '../components/preview/PreviewPanel';
import { ArrowLeftIcon, SettingsIcon } from '../components/common/Icons';
import { UserMenu } from '../components/common/UserMenu';
import { useSplitPane } from '../hooks/useSplitPane';
import { SettingsDialog } from '../components/settings/SettingsDialog';
import { AppMenu } from '../components/common/AppMenu';
import { EditAppDialog } from '../components/common/EditAppDialog';
import { VersionHistoryDialog } from '../components/common/VersionHistoryDialog';
import { PublishButton } from '../components/common/PublishButton';
import { NlInputBar } from '../components/editor/NlInputBar';
import { AiActivityDock } from '../components/common/AiActivityDock';
import { showCaughtError } from '../stores/useErrorDialogStore';
import { selectIsAdmin, useAuthStore } from '../stores/useAuthStore';

type WorkbenchTab = 'canvas' | PreviewPanelTab;
const WORKBENCH_TABS = [
  { id: 'canvas', label: '工作流', icon: GitBranch },
  { id: 'preview', label: '预览', icon: Play },
  { id: 'step', label: '步骤', icon: ListOrdered },
  { id: 'console', label: '控制台', icon: Terminal },
] as const;

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
  const [workbenchTab, setWorkbenchTab] = useState<WorkbenchTab>('canvas');
  const [workbenchExpanded, setWorkbenchExpanded] = useState(false);
  const selectedId = useEditorStore((s) => s.selectedId);
  const setSelected = useEditorStore((s) => s.setSelected);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const isAdmin = useAuthStore(selectIsAdmin);
  const app = loadedApp?.id === id ? loadedApp : null;

  const split = useSplitPane({ initial: 0.56, min: 360, minRightPx: 360 });

  useEffect(() => {
    setWorkbenchTab('canvas');
    setWorkbenchExpanded(false);
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

  const leaveEditor = async (path: string) => {
    try { await flushSave(); navigate(path); }
    catch (error) { showCaughtError(error, '保存失败，请稍后重试'); }
  };
  const selectWorkbench = (tab: WorkbenchTab) => {
    if (tab === 'step' && !selectedId && app?.graph.nodes[0]) setSelected(app.graph.nodes[0].id);
    setWorkbenchTab(tab);
  };
  const workbenchNavigation = <nav aria-label="工作区面板" className="flex min-w-0 flex-1 items-center justify-center gap-0.5">
    {WORKBENCH_TABS.map(({ id: tab, label, icon: Icon }) => <button key={tab} type="button" aria-pressed={workbenchTab === tab} onClick={() => selectWorkbench(tab)} className={`inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-lg px-2 text-xs font-medium transition ${workbenchTab === tab ? 'bg-black/[0.06] text-black' : 'text-black/45 hover:bg-black/[0.03] hover:text-black'}`}><Icon className="hidden h-3.5 w-3.5 xl:block" />{label}</button>)}
    <button onClick={() => setWorkbenchExpanded(!workbenchExpanded)} aria-label={workbenchExpanded ? '恢复对话布局' : '展开工作区'} title={workbenchExpanded ? '恢复对话布局' : '展开工作区'} className="ml-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg text-black/55 hover:bg-black/[0.03]">{workbenchExpanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}</button>
  </nav>;

  if (!app) return <div className="p-10 text-black/55">加载中…</div>;

  return (
    <div className="h-full flex flex-col" ref={split.containerRef}>
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-black/[0.08] bg-white px-4">
        <button className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-black/55 hover:bg-black/5" onClick={() => void leaveEditor('/')} aria-label="返回"><ArrowLeftIcon className="h-4 w-4" /></button>
        <div className="min-w-0 flex-1">
          <input value={app.name} aria-label="应用名称" onChange={(e) => rename(e.target.value)} className="block w-full max-w-md min-w-0 bg-transparent text-sm font-semibold outline-none" />
          <div className="mt-0.5 flex items-center gap-2 text-[11px] text-black/40">
            <span>{app.status === 'published' ? '已发布' : '草稿'}</span><span aria-hidden="true">·</span>
            <span role="status" className={saveStatus === 'error' ? 'text-red-600' : ''} title={saveError ?? undefined}>{saveStatus === 'saving' ? '保存中…' : saveStatus === 'error' ? '保存失败' : saveStatus === 'dirty' ? '待保存' : '已保存'}</span>
          </div>
        </div>
        <AiActivityDock compact />
        <button onClick={() => void leaveEditor(`/apps/${app.id}/view`)} className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg border border-black/10 px-3 text-xs font-medium text-black/65 hover:bg-black/[0.03]"><Play className="h-3.5 w-3.5" />打开应用</button>
        <PublishButton app={app} />
        <AppMenu onEdit={() => setEditOpen(true)} onHistory={() => { void flushSave().then(() => setHistoryOpen(true)).catch((error) => showCaughtError(error, '保存失败，无法打开历史版本')); }} />
        {isAdmin && <button className="grid h-8 w-8 place-items-center rounded-lg hover:bg-black/5" aria-label="设置" onClick={() => setSettingsOpen(true)}><SettingsIcon className="h-4 w-4" /></button>}
        <UserMenu iconClassName="h-4 w-4" />
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
      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        <section aria-label="工作流对话" hidden={workbenchExpanded} className="min-h-0 shrink-0 bg-white" style={{ width: `clamp(360px, ${split.leftPct}%, calc(100% - 364px))` }}>
          <header className="flex h-12 shrink-0 items-center gap-2 border-b border-black/[0.05] px-5 text-sm font-medium"><MessageSquare className="h-4 w-4 text-black/45" />对话</header>
          <div className="h-[calc(100%-48px)]"><NlInputBar key={app.id} empty={app.graph.nodes.length === 0} conversation /></div>
        </section>
        {!workbenchExpanded && <div onPointerDown={split.onPointerDown} className="relative z-10 w-1 shrink-0 cursor-col-resize touch-none bg-black/[0.04] transition-colors before:absolute before:inset-y-0 before:left-1/2 before:w-3 before:-translate-x-1/2 hover:bg-black/15" aria-label="调整对话区宽度" />}
        <section aria-label="工作流工作区" className="relative min-h-0 min-w-0 flex-1 bg-[#F7F8FA]">
          <section hidden={workbenchTab !== 'canvas'} className="h-full">
            <header className="flex h-12 items-center gap-2 border-b border-black/[0.06] bg-white px-2">{workbenchNavigation}</header>
            <div className="h-[calc(100%-48px)]"><Canvas hideNlInput onEditStep={() => selectWorkbench('step')} /></div>
          </section>
          <section hidden={workbenchTab === 'canvas'} className="h-full bg-white"><PreviewPanel tab={workbenchTab === 'canvas' ? 'preview' : workbenchTab} onTabChange={setWorkbenchTab} headerContent={workbenchNavigation} /></section>
        </section>
      </div>
    </div>
  );
}
