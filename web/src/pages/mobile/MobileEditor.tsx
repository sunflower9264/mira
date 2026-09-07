import { useEffect, useRef, useState } from 'react';
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react';
import { GitBranch, ListOrdered, Play, Terminal } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { useEditorStore } from '../../stores/useEditorStore';
import { useRunStore } from '../../stores/useRunStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { Canvas } from '../../components/editor/Canvas';
import { PreviewPanel, type PreviewPanelTab } from '../../components/preview/PreviewPanel';
import { PublishButton } from '../../components/common/PublishButton';
import { EditAppDialog } from '../../components/common/EditAppDialog';
import { VersionHistoryDialog } from '../../components/common/VersionHistoryDialog';
import { AiActivityDock } from '../../components/common/AiActivityDock';
import { ArrowLeftIcon, DotsIcon } from '../../components/common/Icons';

type EditorTab = 'canvas' | PreviewPanelTab;
const TABS = [
  { id: 'canvas', label: '画布', icon: GitBranch },
  { id: 'step', label: '步骤', icon: ListOrdered },
  { id: 'preview', label: '预览', icon: Play },
  { id: 'console', label: '控制台', icon: Terminal },
] as const;

export function MobileEditor() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const pageRef = useRef<HTMLDivElement>(null);
  const loadedApp = useEditorStore((s) => s.app);
  const load = useEditorStore((s) => s.load);
  const setMeta = useEditorStore((s) => s.setMeta);
  const flushSave = useEditorStore((s) => s.flushSave);
  const saveStatus = useEditorStore((s) => s.saveStatus);
  const saveError = useEditorStore((s) => s.saveError);
  const setSelected = useEditorStore((s) => s.setSelected);
  const resetRun = useRunStore((s) => s.reset);
  const restoreActiveRun = useRunStore((s) => s.restoreActiveRun);
  const runId = useRunStore((s) => s.runId);
  const [tab, setTab] = useState<EditorTab>('canvas');
  const [editOpen, setEditOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const app = loadedApp?.id === id ? loadedApp : null;

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    setTab('canvas');
    if (id) void load(id).catch((error) => {
      if (!cancelled) setLoadError(error instanceof Error ? error.message : '应用加载失败');
    });
    return () => { cancelled = true; resetRun(); };
  }, [id, load, resetRun]);

  useEffect(() => {
    if (app && !app.can_edit) navigate(`/m/apps/${app.id}/run`, { replace: true });
  }, [app, navigate]);

  useEffect(() => {
    if (app?.can_edit && runId === null) void restoreActiveRun(app);
  }, [app, runId, restoreActiveRun]);

  useEffect(() => {
    const viewport = window.visualViewport;
    const resize = () => {
      if (!pageRef.current) return;
      pageRef.current.style.height = `${viewport?.height ?? window.innerHeight}px`;
      pageRef.current.style.top = `${viewport?.offsetTop ?? 0}px`;
    };
    resize();
    viewport?.addEventListener('resize', resize);
    viewport?.addEventListener('scroll', resize);
    window.addEventListener('resize', resize);
    return () => {
      viewport?.removeEventListener('resize', resize);
      viewport?.removeEventListener('scroll', resize);
      window.removeEventListener('resize', resize);
    };
  }, []);

  const selectTab = (next: EditorTab) => {
    if (next === 'step' && !useEditorStore.getState().selectedId && app?.graph.nodes[0]) setSelected(app.graph.nodes[0].id);
    setTab(next);
  };

  const openVersions = async () => {
    try { await flushSave(); setVersionsOpen(true); }
    catch (error) { showCaughtError(error, '保存失败，无法记录当前版本'); }
  };

  const leaveEditor = async (path: string) => {
    try { await flushSave(); navigate(path); }
    catch (error) { showCaughtError(error, '保存失败，请稍后重试'); }
  };

  return (
    <div ref={pageRef} className="mira-mobile-editor fixed inset-x-0 top-0 flex h-dvh min-h-0 flex-col overflow-hidden bg-white [&_input]:text-base [&_textarea]:text-base [&_[contenteditable=true]]:text-base" style={{ paddingTop: 'env(safe-area-inset-top)' }}>
      <header className="flex h-14 shrink-0 items-center gap-1 border-b border-black/10 px-2">
        <button aria-label="返回应用列表" onClick={() => void leaveEditor('/m')} className="grid h-11 w-10 shrink-0 place-items-center rounded-xl"><ArrowLeftIcon className="h-5 w-5" /></button>
        <button onClick={() => setEditOpen(true)} disabled={!app} className="min-w-0 flex-1 text-left">
          <div className="truncate text-sm font-semibold">{app?.name ?? '工作流编辑器'}</div>
          <div className={`mt-0.5 text-[11px] ${saveStatus === 'error' ? 'text-red-600' : 'text-black/45'}`} title={saveError ?? undefined} role="status">
            {saveStatus === 'saving' ? '保存中…' : saveStatus === 'error' ? '保存失败' : saveStatus === 'dirty' ? '待保存' : app?.status === 'published' ? '已发布 · 已保存' : '草稿 · 已保存'}
          </div>
        </button>
        <AiActivityDock compact />
        {app?.can_edit && <div className="shrink-0 [&>button]:min-h-10"><PublishButton app={app} /></div>}
        <Menu as="div" className="shrink-0">
          <MenuButton aria-label="应用菜单" className="grid h-11 w-9 place-items-center rounded-xl"><DotsIcon className="h-5 w-5" /></MenuButton>
          <MenuItems anchor="bottom end" className="z-[80] mt-2 w-48 rounded-2xl border border-black/10 bg-white p-1 shadow-xl focus:outline-none">
            <MenuItem><button onClick={() => setEditOpen(true)} className="w-full rounded-xl px-3 py-3 text-left text-sm data-[focus]:bg-black/5">编辑应用信息</button></MenuItem>
            <MenuItem><button onClick={() => void openVersions()} className="w-full rounded-xl px-3 py-3 text-left text-sm data-[focus]:bg-black/5">历史版本</button></MenuItem>
            <MenuItem><button onClick={() => app && void leaveEditor(`/m/apps/${app.id}/run`)} className="w-full rounded-xl px-3 py-3 text-left text-sm data-[focus]:bg-black/5">打开应用</button></MenuItem>
          </MenuItems>
        </Menu>
      </header>
      {saveStatus === 'error' && <div className="shrink-0 border-b border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">{saveError || '更改尚未保存'}</div>}
      <main className="relative min-h-0 flex-1 overflow-hidden">
        {!app || !app.can_edit ? (
          <div className="grid h-full place-items-center p-6 text-center text-sm text-black/50">{loadError ?? '加载中…'}</div>
        ) : <>
          <section aria-label="工作流画布" hidden={tab !== 'canvas'} className="h-full"><Canvas mobile onEditStep={() => selectTab('step')} /></section>
          <section aria-label="工作流面板" hidden={tab === 'canvas'} className="h-full"><PreviewPanel mobile tab={tab === 'canvas' ? 'preview' : tab} onTabChange={setTab} /></section>
        </>}
      </main>
      <nav aria-label="编辑器面板" className="grid shrink-0 grid-cols-4 border-t border-black/10 bg-white px-2 pt-1" style={{ paddingBottom: 'max(4px, env(safe-area-inset-bottom))' }}>
        {TABS.map(({ id: tabId, label, icon: Icon }) => <button key={tabId} aria-current={tab === tabId ? 'page' : undefined} onClick={() => selectTab(tabId)} className={`flex min-h-12 flex-col items-center justify-center gap-1 rounded-xl text-[11px] font-medium ${tab === tabId ? 'bg-black/[0.06] text-black' : 'text-black/45'}`}><Icon className="h-[18px] w-[18px]" />{label}</button>)}
      </nav>
      {app?.can_edit && <>
        <EditAppDialog open={editOpen} onClose={() => setEditOpen(false)} initialName={app.name} initialDescription={app.description} initialCover={app.cover} appId={app.id} onSave={async ({ name, description, cover }) => { setMeta({ name, description, cover }); await flushSave(); }} />
        <VersionHistoryDialog open={versionsOpen} onClose={() => setVersionsOpen(false)} appId={app.id} onCloned={(newAppId) => { setVersionsOpen(false); navigate(`/m/apps/${newAppId}/editor`); }} />
      </>}
    </div>
  );
}
