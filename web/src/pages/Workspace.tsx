import { useEffect, useMemo, useRef, useState } from 'react';
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react';
import { ArrowLeft, BookOpen, Bot, Check, ChevronDown, ChevronRight, Download, File, Folder, GitBranch, MessageSquare, MoreHorizontal, Paperclip, RefreshCw, Search, Send, Settings2, Square, Target, Upload, X } from 'lucide-react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Background, Controls, ReactFlow } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { UserMenu } from '../components/common/UserMenu';
import { SettingsDialog } from '../components/settings/SettingsDialog';
import { selectIsAdmin, useAuthStore } from '../stores/useAuthStore';
import { ConfirmDialog } from '../components/common/ConfirmDialog';
import { AppDialog } from '../components/common/AppDialog';
import { useWorkspaceStore } from '../stores/useWorkspaceStore';
import { showCaughtError } from '../stores/useErrorDialogStore';
import * as api from '../lib/api';
import type { Workspace, WorkspaceEvent, WorkspaceFile, WorkspaceSession, WorkspaceWorkflowProposal, WorkspaceWorkflowRun } from '../types';
import type { DecisionAnswer, RunWaitingRequest, UploadRef } from '../types';
import { DecisionPromptPanel } from '../components/common/DecisionPromptPanel';
import { PromptDialog } from '../components/common/PromptDialog';
import { ImagePreview as WorkspaceImage } from '../components/common/ImagePreview';
import { HtmlOutputPreview } from '../components/preview/HtmlOutputPreview';
import { AiActivityDock } from '../components/common/AiActivityDock';
import { AiActivityIndicator } from '../components/common/AiActivityIndicator';

export function Workspace() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const workspace = useWorkspaceStore((state) => state.workspaces.find((item) => item.id === id));
  const [loading, setLoading] = useState(!workspace);
  useEffect(() => {
    if (workspace || !id) return;
    void api.getWorkspace(id).then((item) => {
      useWorkspaceStore.setState((state) => ({ workspaces: [...state.workspaces.filter((w) => w.id !== item.id), item] }));
    }).catch(() => undefined).finally(() => setLoading(false));
  }, [id, workspace]);
  if (!id || loading) return <div className="flex min-h-full items-center justify-center bg-[#F4F5F7] text-sm text-black/45">加载工作空间…</div>;
  if (!workspace) return <div className="flex min-h-full items-center justify-center bg-[#F4F5F7] text-sm text-black/45">找不到工作空间</div>;
  return <WorkspaceShell workspace={workspace} onBack={() => navigate('/')} />;
}

export function MobileWorkspace() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const workspace = useWorkspaceStore((state) => state.workspaces.find((item) => item.id === id));
  const [loading, setLoading] = useState(!workspace);
  useEffect(() => {
    if (workspace || !id) return;
    void api.getWorkspace(id).then((item) => {
      useWorkspaceStore.setState((state) => ({ workspaces: [...state.workspaces.filter((w) => w.id !== item.id), item] }));
    }).catch(() => undefined).finally(() => setLoading(false));
  }, [id, workspace]);
  if (!id || loading) return <div className="flex min-h-dvh items-center justify-center bg-[#F4F5F7] text-sm text-black/45">加载工作空间…</div>;
  if (!workspace) return <div className="flex min-h-dvh items-center justify-center bg-[#F4F5F7] text-sm text-black/45">找不到工作空间</div>;
  return <WorkspaceShell workspace={workspace} mobile onBack={() => navigate('/m')} />;
}

function WorkspaceShell({ workspace, mobile = false, onBack }: { workspace: Workspace; mobile?: boolean; onBack(): void }) {
  const [overviewOpen, setOverviewOpen] = useState(false);
  const [globalSettingsOpen, setGlobalSettingsOpen] = useState(false);
  const isAdmin = useAuthStore(selectIsAdmin);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const shellRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!mobile || !window.visualViewport) return;
    const viewport = window.visualViewport;
    const resize = () => {
      if (!shellRef.current) return;
      shellRef.current.style.height = `${viewport.height}px`;
      shellRef.current.style.top = `${viewport.offsetTop}px`;
    };
    resize();
    viewport.addEventListener('resize', resize);
    viewport.addEventListener('scroll', resize);
    return () => {
      viewport.removeEventListener('resize', resize);
      viewport.removeEventListener('scroll', resize);
      shellRef.current?.style.removeProperty('height');
      shellRef.current?.style.removeProperty('top');
    };
  }, [mobile]);
  const [tab, setTab] = useState<'chat' | 'files' | 'workflow'>('chat');
  const [sessions, setSessions] = useState<WorkspaceSession[]>([]);
  const [session, setSession] = useState<WorkspaceSession | null>(null);
  const [events, setEvents] = useState<WorkspaceEvent[]>([]);
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [proposals, setProposals] = useState<WorkspaceWorkflowProposal[]>([]);
  const [workflowRuns, setWorkflowRuns] = useState<WorkspaceWorkflowRun[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [newSessionOpen, setNewSessionOpen] = useState(false);
  const [syncingWiki, setSyncingWiki] = useState(false);
  const [filePreview, setFilePreview] = useState<WorkspaceFile | null>(null);
  const [deleteSession, setDeleteSession] = useState<WorkspaceSession | null>(null);
  const [renameSessionOpen, setRenameSessionOpen] = useState(false);
  const [sessionTitle, setSessionTitle] = useState('');
  const [sessionSearchOpen, setSessionSearchOpen] = useState(false);
  const [workspaceSettingsOpen, setWorkspaceSettingsOpen] = useState(false);
  const [focusedWorkflowRunId, setFocusedWorkflowRunId] = useState<string | null>(null);
  const [chatDraft, setChatDraft] = useState<string | null>(null);
  const [sessionOffset, setSessionOffset] = useState(0);
  const [sessionHasMore, setSessionHasMore] = useState(false);
  const sessionStatusRef = useRef<WorkspaceSession['status'] | undefined>(session?.status);
  useEffect(() => { sessionStatusRef.current = session?.status; }, [session?.status]);

  const refresh = async () => {
    setRefreshing(true);
    try {
      const [nextSessions, nextFiles, nextProposals, nextWorkflowRuns] = await Promise.all([
        api.listWorkspaceSessions(workspace.id),
        api.listWorkspaceFiles(workspace.id),
        api.listWorkspaceWorkflowProposals(workspace.id),
        api.listWorkspaceWorkflowRuns(workspace.id),
      ]);
      setSessions(nextSessions.items);
      setSessionHasMore(nextSessions.has_more);
      setSessionOffset(nextSessions.next_offset ?? nextSessions.items.length);
      setSession((current) => nextSessions.items.find((item) => item.id === current?.id) ?? nextSessions.items[0] ?? null);
      setFiles(nextFiles.files);
      setProposals(nextProposals);
      setWorkflowRuns(nextWorkflowRuns);
    } finally {
      setRefreshing(false);
    }
  };
  const selectTab = async (nextTab: typeof tab) => {
    setTab(nextTab);
    if (nextTab !== 'files' && nextTab !== 'workflow') return;
    setRefreshing(true);
    try {
      if (nextTab === 'files') {
        setFiles((await api.listWorkspaceFiles(workspace.id)).files);
      } else {
        const [nextProposals, nextWorkflowRuns] = await Promise.all([
          api.listWorkspaceWorkflowProposals(workspace.id),
          api.listWorkspaceWorkflowRuns(workspace.id),
        ]);
        setProposals(nextProposals);
        setWorkflowRuns(nextWorkflowRuns);
      }
    } finally {
      setRefreshing(false);
    }
  };
  const openWorkflowRun = (runId: string) => {
    setTab('workflow');
    setRefreshing(true);
    void api.listWorkspaceWorkflowRuns(workspace.id)
      .then((nextRuns) => {
        setWorkflowRuns(nextRuns);
        if (nextRuns.some((item) => item.run_id === runId)) setFocusedWorkflowRunId(runId);
        else showCaughtError(new Error('该记录不在最近的工作流运行中'), '找不到工作流记录', '加载失败');
      })
      .catch((error) => showCaughtError(error, '读取工作流记录失败', '加载失败'))
      .finally(() => setRefreshing(false));
  };
  useEffect(() => { void refresh(); }, [workspace.id]);
  useEffect(() => {
    if (!session) return;
    let closed = false;
    let lastId: number | undefined;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await api.listWorkspaceEvents(session.id, lastId);
        if (closed || next.length === 0) return;
        lastId = next[next.length - 1]?.id;
        setEvents((current) => lastId === next[next.length - 1]?.id && current.length === 0 ? next : [...current, ...next.filter((item) => !current.some((existing) => existing.id === item.id))]);
        if (next.some((item) => item.event_type.startsWith('workflow_run_'))) {
          void api.listWorkspaceWorkflowRuns(workspace.id).then(setWorkflowRuns);
        }
        if (next.some((item) => item.event_type === 'workflow_run_finished')) {
          void api.listWorkspaceFiles(workspace.id).then((nextFiles) => setFiles(nextFiles.files));
        }
      } catch {
        // A transient polling failure is retried on the next interval.
      }
    };
    const schedule = () => {
      timer = window.setTimeout(async () => {
        await poll();
        if (!closed) schedule();
      }, sessionStatusRef.current === 'running' ? 300 : 1500);
    };
    setEvents([]);
    void poll().finally(() => { if (!closed) schedule(); });
    return () => { closed = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [session?.id]);

  const addSession = async (title: string) => {
    const created = await api.createWorkspaceSession(workspace.id, title || undefined);
    setSessions((items) => [...items, created]);
    setSession(created);
    setNewSessionOpen(false);
    setTab('chat');
  };
  const renameSession = async () => {
    if (!session) return;
    const title = sessionTitle.trim();
    if (!title || title === session.title) return;
    const updated = await api.updateWorkspaceSession(session.id, title);
    setSession(updated);
    setSessions((items) => items.map((item) => item.id === updated.id ? updated : item));
    setRenameSessionOpen(false);
  };
  const syncWiki = async () => {
    if (syncingWiki) return;
    setSyncingWiki(true);
    try {
      const retry = workspace.wiki_sync_status === 'failed' || workspace.wiki_sync_status === 'conflict';
      const result = await (retry ? api.retryWorkspaceWiki(workspace.id) : api.syncWorkspaceWiki(workspace.id));
      useWorkspaceStore.setState((state) => ({
        workspaces: state.workspaces.map((item) => item.id === workspace.id ? {
          ...item,
          wiki_base_revision_id: result.base_revision_id,
          wiki_sync_status: result.status,
          wiki_sync_error: result.error,
        } : item),
      }));
      if (result.status !== 'ready') {
        showCaughtError(new Error(result.error || 'Wiki 同步未完成'), 'Wiki 同步未完成', '同步失败');
      }
    } catch (error) {
      showCaughtError(error, 'Wiki 同步失败', '同步失败');
    } finally {
      setSyncingWiki(false);
    }
  };

  const sessionList = <>
            <div className="mb-2 flex items-center justify-between px-2"><span className="text-xs font-semibold uppercase tracking-wider text-black/45">会话</span><div className="flex items-center gap-1"><button type="button" onClick={() => { setSessionsOpen(false); setSessionSearchOpen(true); }} className="grid h-11 w-11 sm:h-7 sm:w-7 place-items-center rounded-full hover:bg-black/5" aria-label="搜索会话"><Search className="h-3.5 w-3.5" /></button><button type="button" onClick={() => { setSessionsOpen(false); setNewSessionOpen(true); }} className="grid h-11 w-11 sm:h-7 sm:w-7 place-items-center rounded-full hover:bg-black/5" aria-label="新建会话"><PlusIcon /></button></div></div>
            <div className="space-y-1">{sessions.map((item) => <button type="button" key={item.id} onClick={() => { setSession(item); setTab('chat'); setSessionsOpen(false); }} title={item.title} aria-current={session?.id === item.id && tab === 'chat' ? 'true' : undefined} className={`flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm ${session?.id === item.id && tab === 'chat' ? (mobile ? 'bg-black text-white' : 'bg-black/[0.06] font-medium text-black') : 'text-black/60 hover:bg-black/5'}`}><MessageSquare className="h-3.5 w-3.5 shrink-0" /><span className="min-w-0 flex-1 truncate">{item.title}</span>{item.status === 'running' ? <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-400" /> : null}</button>)}{sessionHasMore ? <button type="button" onClick={async () => { const page = await api.listWorkspaceSessions(workspace.id, { offset: sessionOffset }); setSessions((current) => [...current, ...page.items]); setSessionHasMore(page.has_more); setSessionOffset(page.next_offset ?? sessionOffset + page.items.length); }} className="w-full rounded-xl px-3 py-2 text-xs text-black/40 hover:bg-black/5">加载更多</button> : null}</div>
  </>;

  return (
    <div ref={shellRef} className={`flex h-dvh flex-col overflow-hidden bg-[#F4F5F7] text-[#0B0B0F] ${mobile ? 'fixed inset-x-0 top-0 bg-white' : ''}`}>
      <main className={`flex min-h-0 w-full flex-1 flex-col overflow-hidden ${mobile ? 'pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)]' : ''}`}>
        {mobile ? <header className="flex h-14 shrink-0 items-center gap-1 border-b border-black/5 bg-white px-2">
          <button type="button" onClick={() => tab === 'chat' ? onBack() : setTab('chat')} className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-black/55 hover:bg-black/5" aria-label={tab === 'chat' ? '返回' : '返回对话'}><ArrowLeft className="h-5 w-5" /></button>
          <button type="button" onClick={() => setSessionsOpen(true)} className="flex h-11 min-w-0 flex-1 items-center gap-1.5 text-left" aria-label="切换会话">
            <span className="min-w-0"><span className="block truncate text-[11px] leading-4 text-black/40">{workspace.name}</span><span className="block truncate text-sm font-semibold leading-5">{tab === 'files' ? '项目文件' : tab === 'workflow' ? '工作流' : session?.title ?? '选择会话'}</span></span><ChevronDown className="h-3.5 w-3.5 shrink-0 text-black/40" />
          </button>
          <AiActivityDock compact />
          <Menu as="div" className="relative shrink-0">
            <MenuButton type="button" aria-label="工作空间菜单" className="grid h-11 w-11 place-items-center rounded-full text-black/55 hover:bg-black/5"><MoreHorizontal className="h-5 w-5" /></MenuButton>
            <MenuItems anchor="bottom end" className="z-[75] mt-1 max-h-[var(--anchor-max-height)] w-52 overflow-y-auto rounded-2xl bg-white p-1 shadow-xl ring-1 ring-black/5 focus:outline-none">
              {([['chat', '对话', MessageSquare], ['files', '文件', Folder], ['workflow', '工作流', GitBranch]] as const).map(([key, label, Icon]) => <MenuItem key={key}>{({ focus }) => <button type="button" onClick={() => void selectTab(key)} className={`flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-sm ${focus ? 'bg-black/5' : ''}`}><Icon className="h-4 w-4 text-black/45" />{label}{tab === key ? <Check className="ml-auto h-4 w-4" /> : null}</button>}</MenuItem>)}
              <div className="my-1 border-t border-black/5" />
              <MenuItem>{({ focus }) => <button type="button" onClick={() => setOverviewOpen(true)} className={`flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-sm ${focus ? 'bg-black/5' : ''}`}><Target className="h-4 w-4 text-black/45" />当前目标与状态</button>}</MenuItem>
              <MenuItem disabled={syncingWiki}>{({ focus }) => <button type="button" onClick={() => void syncWiki()} disabled={syncingWiki} className={`flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-sm disabled:opacity-40 ${focus ? 'bg-black/5' : ''}`}><RefreshCw className="h-4 w-4 text-black/45" />{syncingWiki ? '同步中…' : '同步 Wiki'}</button>}</MenuItem>
              <MenuItem>{({ focus }) => <button type="button" onClick={() => setWorkspaceSettingsOpen(true)} className={`flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-sm ${focus ? 'bg-black/5' : ''}`}><Settings2 className="h-4 w-4 text-black/45" />工作空间设置</button>}</MenuItem>
              <MenuItem disabled={refreshing}>{({ focus }) => <button type="button" onClick={() => void refresh()} disabled={refreshing} className={`flex min-h-11 w-full items-center gap-3 rounded-xl px-3 text-sm disabled:opacity-40 ${focus ? 'bg-black/5' : ''}`}><RefreshCw className="h-4 w-4 text-black/45" />刷新</button>}</MenuItem>
            </MenuItems>
          </Menu>
        </header> : null}
        <div className={`${mobile ? 'flex min-h-0 flex-1 flex-col' : 'grid min-h-0 flex-1 grid-cols-[224px_minmax(0,1fr)] xl:grid-cols-[248px_minmax(0,1fr)]'}`}>
          {!mobile ? <aside className="flex min-h-0 flex-col border-r border-black/[0.08] bg-[#F7F8FA]" aria-label="项目导航">
            <div className="flex h-14 shrink-0 items-center justify-between gap-2 px-4">
              <button type="button" onClick={onBack} className="flex h-9 items-center gap-2 rounded-lg pr-2 text-sm text-black/55 hover:text-black" aria-label="返回"><ArrowLeft className="h-4 w-4" /><span className="text-lg font-semibold tracking-tight text-black">Mira</span></button>
              <Link to="/wiki" aria-label="打开 Wiki" title="Wiki" className="grid h-9 w-9 place-items-center rounded-lg text-black/45 hover:bg-black/5"><BookOpen className="h-4 w-4" /></Link>
            </div>
            <div className="shrink-0 px-5 pb-4 pt-2"><div className="mb-1 text-[11px] font-medium text-black/35">工作空间</div><h1 title={workspace.name} className="line-clamp-2 text-sm font-semibold leading-6 tracking-tight">{workspace.name}</h1></div>
            <nav className="shrink-0 space-y-1 px-3 pb-4" aria-label="工作空间视图">{([['chat', '项目对话', MessageSquare], ['files', '项目文件', Folder], ['workflow', '工作流', GitBranch]] as const).map(([key, label, Icon]) => <button key={key} type="button" onClick={() => void selectTab(key)} aria-pressed={tab === key} className={`flex h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-sm ${tab === key ? 'bg-white font-semibold text-black shadow-sm ring-1 ring-black/[0.06]' : 'text-black/55 hover:bg-black/[0.04]'}`}><Icon className="h-4 w-4" /><span className="flex-1">{label}</span>{key === 'workflow' && proposals.some((item) => item.status === 'pending') ? <span className="h-1.5 w-1.5 rounded-full bg-amber-500" aria-label="有待确认提案" /> : null}</button>)}</nav>
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 pb-3">{sessionList}</div>
            <div className="shrink-0 space-y-1 border-t border-black/[0.06] p-3">
              <button type="button" onClick={() => setOverviewOpen(true)} className="flex h-9 w-full items-center gap-3 rounded-lg px-3 text-left text-xs text-black/55 hover:bg-black/5"><Target className="h-4 w-4" />当前目标与状态</button>
              <Menu as="div" className="relative">
                <MenuButton type="button" aria-label="工作空间设置" className="flex h-9 w-full items-center gap-3 rounded-lg px-3 text-left text-xs text-black/55 hover:bg-black/5"><Settings2 className="h-4 w-4" /><span className="flex-1">工作空间设置</span><span className={`h-1.5 w-1.5 rounded-full ${workspace.runtime_status === 'ready' ? 'bg-emerald-500' : workspace.runtime_status === 'error' ? 'bg-red-500' : 'bg-black/30'}`} aria-label={`运行环境${workspaceRuntimeLabel(workspace.runtime_status)}`} /></MenuButton>
                <MenuItems anchor="top start" className="z-[75] mb-1 w-52 rounded-xl bg-white p-1 text-sm shadow-xl ring-1 ring-black/10 focus:outline-none">
                  <div className="px-3 py-2 text-xs text-black/40">运行环境 · {workspaceRuntimeLabel(workspace.runtime_status)}</div>
                  <MenuItem><button type="button" onClick={() => setWorkspaceSettingsOpen(true)} className="flex min-h-9 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5"><GitBranch className="h-4 w-4 text-black/45" />Git 与项目设置</button></MenuItem>
                  <MenuItem disabled={syncingWiki}><button type="button" disabled={syncingWiki} onClick={() => void syncWiki()} className="flex min-h-9 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5 disabled:opacity-40"><RefreshCw className={`h-4 w-4 text-black/45 ${syncingWiki ? 'animate-spin' : ''}`} />{syncingWiki ? '同步中…' : '同步 Wiki'}</button></MenuItem>
                  <MenuItem disabled={refreshing}><button type="button" disabled={refreshing} onClick={() => void refresh()} className="flex min-h-9 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5 disabled:opacity-40"><RefreshCw className="h-4 w-4 text-black/45" />刷新工作空间</button></MenuItem>
                </MenuItems>
              </Menu>
              <div className="flex items-center justify-between gap-1 pt-2"><UserMenu iconClassName="h-4 w-4" />{isAdmin ? <button type="button" onClick={() => setGlobalSettingsOpen(true)} aria-label="系统设置" title="系统设置" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-black/45 hover:bg-black/5"><Settings2 className="h-4 w-4" /></button> : null}</div>
            </div>
          </aside> : null}
          <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white">
            {!mobile && tab !== 'chat' ? <div className="flex h-14 shrink-0 items-center justify-between border-b border-black/[0.06] px-6"><button type="button" onClick={() => setTab('chat')} className="flex items-center gap-2 text-sm text-black/55 hover:text-black"><ArrowLeft className="h-4 w-4" />返回对话</button><AiActivityDock compact /></div> : null}

            <div className={`min-h-0 flex-1 ${tab === 'chat' ? '' : mobile ? 'p-4' : 'p-6 xl:px-8'} ${tab === 'chat' ? 'overflow-hidden' : 'overflow-y-auto'}`}>
              <div hidden={tab !== 'chat'} className="h-full"><WorkspaceChat desktopActivity={!mobile && tab === 'chat' ? <AiActivityDock compact /> : undefined} mobile={mobile} onSelectSession={() => setSessionsOpen(true)} workspace={workspace} session={session} events={events} workflowRuns={workflowRuns} draft={chatDraft} onDraftApplied={() => setChatDraft(null)} onEvents={setEvents} onSessionUpdate={(next) => { setSession(next); setSessions((items) => items.map((item) => item.id === next.id ? next : item)); }} onNew={() => setNewSessionOpen(true)} onDelete={() => session && setDeleteSession(session)} onRename={() => { if (session) { setSessionTitle(session.title); setRenameSessionOpen(true); } }} onOpenWorkflowRun={openWorkflowRun} /></div>
              {tab === 'files' ? <WorkspaceFiles workspace={workspace} files={files} onRefresh={() => void refresh()} onPreview={setFilePreview} /> : null}
              {tab === 'workflow' ? <WorkspaceWorkflow workspace={workspace} proposals={proposals} runs={workflowRuns} selectedRunId={focusedWorkflowRunId} onSelectedRunHandled={() => setFocusedWorkflowRunId(null)} onReturnToChat={(run, repair) => { const sourceSession = sessions.find((item) => item.id === run.session_id); if (sourceSession) setSession(sourceSession); if (repair) setChatDraft(workflowRepairPrompt(run)); setTab('chat'); }} onRefresh={() => void refresh()} /> : null}
            </div>
          </section>
        </div>
      </main>
      <AppDialog open={overviewOpen} onClose={() => setOverviewOpen(false)} title="当前目标与状态" description={workspace.description}>
        {overviewOpen ? <WorkspaceOverview session={session} events={events} proposals={proposals} workflowRuns={workflowRuns} onOpenChat={() => { setOverviewOpen(false); setTab('chat'); }} onOpenWorkflowRun={(runId) => { setOverviewOpen(false); openWorkflowRun(runId); }} onOpenWorkflow={() => { setOverviewOpen(false); void selectTab('workflow'); }} /> : null}
      </AppDialog>
      <AppDialog open={mobile && sessionsOpen} onClose={() => setSessionsOpen(false)} title="切换会话"><div className="max-h-[60dvh] overflow-y-auto overscroll-contain">{sessionList}</div></AppDialog>
      <NewSessionDialog open={newSessionOpen} onClose={() => setNewSessionOpen(false)} onCreate={addSession} />
      <PromptDialog open={renameSessionOpen} onClose={() => setRenameSessionOpen(false)} onConfirm={renameSession} title="重命名会话" inputLabel="会话名称" value={sessionTitle} onChange={setSessionTitle} disabled={!sessionTitle.trim() || sessionTitle.trim() === session?.title} />
      <FilePreviewDialog workspace={workspace} file={filePreview} onClose={() => setFilePreview(null)} />
      <SessionSearchDialog open={sessionSearchOpen} workspaceId={workspace.id} onClose={() => setSessionSearchOpen(false)} onSelect={(next) => { setSessions((current) => current.some((item) => item.id === next.id) ? current : [next, ...current]); setSession(next); setTab('chat'); setSessionSearchOpen(false); }} />
      {!mobile && isAdmin ? <SettingsDialog open={globalSettingsOpen} onClose={() => setGlobalSettingsOpen(false)} /> : null}
      <WorkspaceSettingsDialog open={workspaceSettingsOpen} workspace={workspace} onClose={() => setWorkspaceSettingsOpen(false)} />
      <ConfirmDialog open={deleteSession !== null} onClose={() => setDeleteSession(null)} onConfirm={async () => { if (deleteSession) { await api.deleteWorkspaceSession(deleteSession.id); setSessions((items) => items.filter((item) => item.id !== deleteSession.id)); if (session?.id === deleteSession.id) setSession(null); setDeleteSession(null); } }} title="删除会话？" description="会话记录和 Codex thread 将被永久删除。" confirmLabel="删除" tone="danger" />
    </div>
  );
}

function WorkspaceOverview({
  compact = false,
  session,
  events,
  proposals,
  workflowRuns,
  onOpenChat,
  onOpenWorkflow,
  onOpenWorkflowRun,
}: {
  compact?: boolean;
  session: WorkspaceSession | null;
  events: WorkspaceEvent[];
  proposals: WorkspaceWorkflowProposal[];
  workflowRuns: WorkspaceWorkflowRun[];
  onOpenChat(): void;
  onOpenWorkflow(): void;
  onOpenWorkflowRun(runId: string): void;
}) {
  const [goal, setGoal] = useState('');
  const [goalStatus, setGoalStatus] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [goalOpen, setGoalOpen] = useState(false);
  const [goalLoading, setGoalLoading] = useState(false);
  const [goalLoadFailed, setGoalLoadFailed] = useState(false);
  const [goalReload, setGoalReload] = useState(0);
  const [goalSaving, setGoalSaving] = useState(false);

  useEffect(() => {
    let closed = false;
    setGoal('');
    setGoalStatus(null);
    setDraft('');
    setGoalLoading(false);
    setGoalLoadFailed(false);
    if (!session?.thread_id) return;
    setGoalLoading(true);
    void api.getWorkspaceGoal(session.id)
      .then((result) => {
        if (closed) return;
        const objective = result?.objective?.trim() ?? '';
        setGoal(objective);
        setGoalStatus(result?.status ?? null);
        setDraft(objective);
      })
      .catch(() => { if (!closed) setGoalLoadFailed(true); })
      .finally(() => { if (!closed) setGoalLoading(false); });
    return () => { closed = true; };
  }, [goalReload, session?.id, session?.thread_id]);

  const pendingProposal = proposals.find((item) => item.status === 'pending');
  const activeWorkflowEvent = [...collapseWorkspaceEvents(events)].reverse().find((item) => item.event_type === 'workflow_run_started' || item.event_type === 'workflow_run_waiting');
  const activeRunId = typeof activeWorkflowEvent?.payload.run_id === 'string' ? activeWorkflowEvent.payload.run_id : undefined;
  const activeRun = workflowRuns.find((item) => item.run_id === activeRunId)
    ?? workflowRuns.find((item) => ['pending', 'running', 'waiting_for_user'].includes(item.status));
  const failedRun = workflowRuns[0]?.status === 'failed' ? workflowRuns[0] : undefined;
  const waitingForUser = hasWorkspaceWaitingRequest(events) || session?.status === 'waiting';
  const status = waitingForUser
    ? { label: '等待你回复', tone: 'bg-amber-500' }
    : session?.status === 'running'
      ? { label: activeRun ? '工作流运行中' : 'Codex 处理中', tone: 'bg-emerald-500' }
      : activeRun
        ? { label: activeRun.status === 'waiting_for_user' ? '工作流等待回复' : '工作流运行中', tone: activeRun.status === 'waiting_for_user' ? 'bg-amber-500' : 'bg-emerald-500' }
      : session?.status === 'error'
        ? { label: '需要处理', tone: 'bg-red-500' }
        : pendingProposal
          ? { label: '工作流待确认', tone: 'bg-amber-500' }
          : failedRun
            ? { label: '工作流运行失败', tone: 'bg-red-500' }
            : null;
  const nextAction = waitingForUser
    ? { label: '回复', onClick: onOpenChat }
    : activeRun
      ? { label: '查看', onClick: () => onOpenWorkflowRun(activeRun.run_id) }
      : pendingProposal
        ? { label: '确认', onClick: onOpenWorkflow }
        : failedRun
          ? { label: '处理', onClick: () => onOpenWorkflowRun(failedRun.run_id) }
          : session?.status === 'error'
            ? { label: '返回对话', onClick: onOpenChat }
            : null;
  const goalLabel = goalStatus === 'complete' ? '已完成目标' : goalStatus === 'blocked' ? '受阻目标' : '当前目标';

  const saveGoal = async () => {
    if (!session?.thread_id || !draft.trim()) return;
    setGoalSaving(true);
    try {
      const result = await api.saveWorkspaceGoal(session.id, { objective: draft.trim(), status: 'active' });
      setGoal(result.objective?.trim() ?? draft.trim());
      setGoalStatus(result.status ?? 'active');
      setGoalOpen(false);
    } catch (error) {
      showCaughtError(error, '保存当前目标失败', '保存失败');
    } finally {
      setGoalSaving(false);
    }
  };
  const clearGoal = async () => {
    if (!session?.thread_id) return;
    setGoalSaving(true);
    try {
      await api.clearWorkspaceGoal(session.id);
      setGoal('');
      setGoalStatus(null);
      setDraft('');
      setGoalOpen(false);
    } catch (error) {
      showCaughtError(error, '清除当前目标失败', '清除失败');
    } finally {
      setGoalSaving(false);
    }
  };

  return (
    <section className={`flex shrink-0 flex-wrap items-center gap-3 bg-white ${compact ? 'border-b border-black/[0.06] px-6 py-2' : 'mb-4 rounded-[20px] border border-black/5 px-4 py-3 shadow-card'}`}>
      <button type="button" disabled={!session?.thread_id || goalLoading} onClick={() => { if (goalLoadFailed) { setGoalReload((value) => value + 1); return; } setDraft(goal); setGoalOpen(true); }} className={`min-w-0 flex-1 basis-44 text-left disabled:cursor-default ${compact ? 'flex items-center gap-3' : ''}`}>
        <span className="flex items-center gap-1.5 shrink-0 text-[10px] font-medium uppercase tracking-wider text-black/35"><Target className="h-3 w-3" />{goalLabel}</span>
        <span className={`${compact ? 'min-w-0' : 'mt-1'} block truncate text-sm font-medium text-black/70`}>{goalLoading ? '读取中…' : goalLoadFailed ? '重新读取' : goal || (session?.thread_id ? '设置目标' : '尚未开始')}</span>
      </button>
      {status ? <div className="flex w-full shrink-0 items-center justify-between gap-3 border-t border-black/[0.06] pt-3 sm:w-auto sm:justify-start sm:border-l sm:border-t-0 sm:pl-4 sm:pt-0"><span className="flex items-center gap-2 text-sm font-medium text-black/65"><span className={`h-2 w-2 shrink-0 rounded-full ${status.tone}`} />{status.label}</span>{nextAction ? <button type="button" onClick={nextAction.onClick} className="inline-flex items-center gap-0.5 rounded-full bg-black px-3 py-1.5 text-xs font-medium text-white hover:bg-black/85">{nextAction.label}<ChevronRight className="h-3.5 w-3.5" /></button> : null}</div> : null}
      <AppDialog open={goalOpen} onClose={() => !goalSaving && setGoalOpen(false)} title="当前目标" footer={<><button type="button" onClick={() => setGoalOpen(false)} className="rounded-full border border-black/10 px-4 py-2 text-sm">取消</button>{goal ? <button type="button" disabled={goalSaving} onClick={() => void clearGoal()} className="rounded-full px-4 py-2 text-sm text-red-600 hover:bg-red-50 disabled:opacity-40">清除</button> : null}<button type="button" disabled={goalSaving || !draft.trim()} onClick={() => void saveGoal()} className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{goalSaving ? '保存中…' : '保存'}</button></>}>
        <textarea aria-label="当前目标" autoFocus value={draft} onChange={(event) => setDraft(event.target.value)} className="min-h-28 w-full resize-none rounded-xl border border-black/10 px-3 py-2 text-sm leading-6 outline-none focus:border-black/30" />
      </AppDialog>
    </section>
  );
}

function SessionSearchDialog({ open, workspaceId, onClose, onSelect }: { open: boolean; workspaceId: string; onClose(): void; onSelect(session: WorkspaceSession): void }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<WorkspaceSession[]>([]);
  useEffect(() => {
    if (!open) return;
    const normalized = query.trim();
    if (!normalized) { setResults([]); return; }
    const timer = window.setTimeout(() => { void api.listWorkspaceSessions(workspaceId, { query: normalized, limit: 50 }).then((page) => setResults(page.items)).catch(() => setResults([])); }, 180);
    return () => window.clearTimeout(timer);
  }, [open, workspaceId, query]);
  useEffect(() => { if (!open) setQuery(''); }, [open]);
  return <AppDialog open={open} onClose={onClose} title="搜索会话" widthClassName="max-w-xl"><input aria-label="搜索标题或对话内容" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题或对话内容" className="h-11 w-full rounded-xl border border-black/10 px-3 text-sm outline-none focus:border-black/25" /><div className="mt-3 max-h-[50vh] space-y-1 overflow-y-auto">{results.length ? results.map((item) => <button type="button" key={item.id} onClick={() => onSelect(item)} className="w-full rounded-xl px-3 py-3 text-left hover:bg-black/[0.04]"><div className="flex items-center gap-2 text-sm font-medium"><MessageSquare className="h-3.5 w-3.5 text-black/40" />{item.title}</div>{item.match_context ? <div className="mt-1 line-clamp-2 text-xs leading-5 text-black/45">{item.match_context}</div> : <div className="mt-1 text-xs text-black/35">{item.updated_at}</div>}</button>) : <div className="py-10 text-center text-xs text-black/35">{query ? '没有匹配的会话' : '输入关键词搜索'}</div>}</div></AppDialog>;
}

function WorkspaceChat({ desktopActivity, mobile, onSelectSession, workspace, session, events, workflowRuns, draft, onDraftApplied, onEvents, onSessionUpdate, onNew, onDelete, onRename, onOpenWorkflowRun }: { desktopActivity?: React.ReactNode; mobile: boolean; onSelectSession(): void; workspace: Workspace; session: WorkspaceSession | null; events: WorkspaceEvent[]; workflowRuns: WorkspaceWorkflowRun[]; draft: string | null; onDraftApplied(): void; onEvents(next: WorkspaceEvent[]): void; onSessionUpdate(next: WorkspaceSession): void; onNew(): void; onDelete(): void; onRename(): void; onOpenWorkflowRun(runId: string): void }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<UploadRef[]>([]);
  const [uploading, setUploading] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [actionStatus, setActionStatus] = useState('');
  const [submittedDecisionId, setSubmittedDecisionId] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<{ skills: string[]; mcp: string[] }>({ skills: [], mcp: [] });
  const attachmentInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const input = composerRef.current;
    if (!mobile || !input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 112)}px`;
  }, [mobile, text, session?.id]);
  const followMessagesRef = useRef(true);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const container = messagesRef.current;
    if (!mobile || !container) return;
    const observer = new ResizeObserver(() => {
      if (followMessagesRef.current) container.scrollTop = container.scrollHeight;
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [mobile, session?.id]);
  const waiting = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (event.event_type === 'decision_submitted') return null;
      const request = workspaceWaitingRequest(event);
      if (request) return request.request_id === submittedDecisionId ? null : { request, turnId: event.turn_id ?? turnId };
      const status = String(event.payload?.status ?? '');
      if (['success', 'failed', 'cancelled', 'completed'].includes(status)) return null;
    }
    return null;
  }, [events, submittedDecisionId, turnId]);
  useEffect(() => {
    const container = messagesRef.current;
    if (container && followMessagesRef.current) container.scrollTo({ top: container.scrollHeight });
  }, [events]);
  useEffect(() => { followMessagesRef.current = true; }, [session?.id]);
  useEffect(() => { void api.getSettings().then((settings) => { setModels(settings.supported_models); setCapabilities({ skills: settings.skills.filter((item) => item.enabled).map((item) => item.name), mcp: settings.mcp_servers.filter((item) => item.enabled).map((item) => item.name) }); }).catch(() => undefined); }, []);
  useEffect(() => { setSubmittedDecisionId(null); }, [session?.id]);
  useEffect(() => {
    if (!draft) return;
    setText(draft);
    onDraftApplied();
  }, [draft, onDraftApplied]);
  const lastEvent = events.at(-1);
  const terminalStatus = lastEvent?.event_type === 'turn_completed'
    ? String(lastEvent.payload?.status ?? '')
    : lastEvent?.event_type === 'error' ? 'failed' : '';
  useEffect(() => {
    if (!session) return;
    const nextStatus = waiting
      ? 'waiting'
      : terminalStatus === 'failed'
        ? 'error'
        : ['success', 'cancelled', 'completed'].includes(terminalStatus)
          ? 'idle'
          : null;
    if (nextStatus && session.status !== nextStatus) onSessionUpdate({ ...session, status: nextStatus });
  }, [session?.id, session?.status, terminalStatus, waiting?.request.request_id]);
  if (!session) return <div className="relative flex h-full flex-col items-center justify-center gap-3 text-center">{!mobile ? <div className="absolute right-6 top-3">{desktopActivity}</div> : null}<Bot className="h-8 w-8 text-black/20" /><div className="mt-3 text-sm font-medium text-black/65">选择或创建一个会话</div><button type="button" onClick={onNew} className="min-h-11 rounded-full bg-black px-5 text-sm text-white">新建会话</button>{mobile ? <button type="button" onClick={onSelectSession} className="min-h-11 rounded-full px-5 text-sm text-black/60">选择会话</button> : null}</div>;
  const send = async () => {
    if (!text.trim() || sending || uploading || session.status === 'running' || waiting) return;
    const command = text.trim().split(/\s+/, 1)[0];
    if (command === '/new') { setText(''); onNew(); return; }
    if (command === '/status') { setActionStatus(`Runtime ${workspace.runtime_status} · Session ${session.status} · Skills ${capabilities.skills.length} · MCP ${capabilities.mcp.length}`); setText(''); return; }
    if (command === '/model') { const requested = text.trim().slice('/model'.length).trim(); if (requested && models.includes(requested)) setModel(requested); setActionStatus(requested ? `下一轮模型：${requested}` : `当前模型：${model || '默认'}`); setText(''); return; }
    if (command === '/compact' || command === '/review') { setSending(true); try { await api.runWorkspaceSessionAction(session.id, command === '/compact' ? 'compact' : 'review', text.trim().slice(command.length).trim() || undefined); setActionStatus(command === '/compact' ? '上下文压缩完成' : 'Review 已启动'); setText(''); } finally { setSending(false); } return; }
    if (command.startsWith('/') && command !== '/') { setActionStatus(`不支持 ${command}。可用：/new、/compact、/review、/model、/status；不提供 /diff。`); return; }
    setSending(true);
    try {
      const turn = await api.startWorkspaceTurn(session.id, { text: text.trim(), attachments: attachments.map((item) => ({ id: item.id, name: item.name })), model: model || undefined });
      followMessagesRef.current = true;
      setTurnId(turn.id);
      onSessionUpdate({ ...session, status: 'running', last_turn_at: new Date().toISOString() });
      setText('');
      setAttachments([]);
      const next = await api.listWorkspaceEvents(session.id, events.at(-1)?.id);
      onEvents([...events, ...next]);
    } finally { setSending(false); }
  };
  const uploadAttachments = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (!files.length) return;
    setUploading(true);
    try {
      const uploaded = await Promise.all(files.map((file) => api.uploadFile(file)));
      setAttachments((current) => [...current, ...uploaded]);
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };
  const isRunning = session.status === 'running' || sending;
  const activeTurnId = turnId ?? (session.status === 'running' || session.status === 'waiting' ? events.at(-1)?.turn_id ?? null : null);
  const hasAssistantOutput = activeTurnId !== null && events.some((event) =>
    event.turn_id === activeTurnId && (event.event_type === 'message_delta' || (event.event_type === 'message_completed' && String(event.payload.role ?? 'assistant') !== 'user')),
  );
  const submitDecision = async (answers: DecisionAnswer[]) => {
    if (!waiting?.turnId) return;
    setSending(true);
    try {
      await api.resumeWorkspaceTurn(waiting.turnId, { request_id: waiting.request.request_id, answers });
      setSubmittedDecisionId(waiting.request.request_id);
      onSessionUpdate({ ...session, status: 'running' });
    } catch (error) {
      showCaughtError(error, '提交回复失败', '回复失败');
    } finally {
      setSending(false);
    }
  };
  const runSessionAction = async (kind: 'compact' | 'review' | 'archive') => {
    setSending(true);
    try {
      await api.runWorkspaceSessionAction(session.id, kind);
      setActionStatus(kind === 'compact' ? '上下文压缩完成' : kind === 'review' ? 'Review 已启动' : 'Session 已归档');
    } catch (error) {
      showCaughtError(error, '执行会话操作失败', '操作失败');
    } finally {
      setSending(false);
    }
  };
  return <div className="flex h-full min-h-0 flex-col">{!mobile ? <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-black/[0.06] px-6"><div className="flex min-w-0 flex-1 items-center gap-3"><h2 className="truncate text-sm font-semibold">{session.title}</h2><span className="hidden shrink-0 text-xs text-black/35 lg:block">{session.thread_id ? `${model || '默认模型'}` : '尚未开始对话'}</span></div>{desktopActivity}<SessionActionsMenu session={session} busy={sending} onNew={onNew} onRename={onRename} onDelete={onDelete} onAdvanced={() => setToolsOpen(true)} onAction={(kind) => void runSessionAction(kind)} /></div> : null}{actionStatus ? <div className="mb-3 shrink-0 rounded-xl border border-black/5 bg-black/[0.025] px-3 py-2 text-xs text-black/55">{actionStatus}</div> : null}<div ref={messagesRef} onScroll={(event) => { const node = event.currentTarget; followMessagesRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80; }} aria-label="对话记录" aria-live="polite" className={`min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain ${mobile ? 'px-4 py-4' : 'px-6 py-6 xl:px-[max(32px,calc((100%-960px)/2))]'}`}>{events.length === 0 && !isRunning ? <div className="flex h-full items-center justify-center text-xs text-black/35">告诉 Codex 你想在这个项目中完成什么。</div> : groupWorkspaceEvents(events).map((group) => <section key={group.key} className="space-y-3 border-b border-black/[0.06] pb-3 last:border-b-0 last:pb-0">{group.events.map((event) => <EventBubble key={event.id} flat={!mobile} event={event} workspaceId={workspace.id} workflowRun={workflowRuns.find((run) => run.run_id === event.payload?.run_id)} onOpenWorkflowRun={onOpenWorkflowRun} />)}</section>)}{isRunning && !waiting && !hasAssistantOutput ? <div className="flex justify-start"><div className="rounded-2xl bg-white px-3.5 py-2.5 text-sm text-black/55 shadow-sm"><AiActivityIndicator status={sending ? 'queued' : 'working'} label={sending ? '正在准备' : 'Codex 正在处理'} compact /></div></div> : null}{waiting ? <DecisionPromptPanel context={waiting.request.context} groups={waiting.request.groups} disabled={sending} onComplete={(answers) => void submitDecision(answers)} /> : null}</div><div className={mobile ? 'shrink-0 border-t border-black/5 bg-white px-3 py-2' : 'mx-6 mb-5 mt-2 shrink-0 rounded-xl border border-black/15 bg-white p-2 shadow-sm xl:mx-[max(32px,calc((100%-960px)/2))]'}>{attachments.length ? <div className="flex flex-wrap gap-1.5 px-2 pt-1">{attachments.map((item) => <button type="button" key={item.id} onClick={() => setAttachments((current) => current.filter((entry) => entry.id !== item.id))} className="inline-flex items-center gap-1 rounded-full bg-black/[0.05] px-2.5 py-1 text-[11px] text-black/60">{item.name}<X className="h-3 w-3" /></button>)}</div> : null}<div className={mobile ? 'flex items-end gap-1 rounded-[26px] border border-black/10 bg-[#F7F7F8] p-1' : ''}>
        {mobile ? <><input ref={attachmentInputRef} type="file" multiple disabled={isRunning || uploading} className="hidden" onChange={(event) => void uploadAttachments(event)} /><SessionActionsMenu session={session} busy={sending} onNew={onNew} onRename={onRename} onDelete={onDelete} onAdvanced={() => setToolsOpen(true)} onAction={(kind) => void runSessionAction(kind)} onAttach={() => attachmentInputRef.current?.click()} uploading={uploading} attachDisabled={isRunning || uploading} /></> : null}
        <textarea ref={composerRef} value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !mobile && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} rows={mobile ? 1 : 2} aria-label="消息" placeholder={mobile ? '回复 Codex…' : '向 Codex 描述下一步…'} className={`w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 outline-none placeholder:text-black/35 ${mobile ? 'min-h-11 min-w-0 flex-1 py-2.5' : 'min-h-16'}`} disabled={isRunning || waiting !== null} /><div className="flex shrink-0 items-center justify-end px-1"><div className="flex items-center gap-1">{!mobile ? <label className={`grid h-11 w-11 place-items-center rounded-full text-black/35 sm:h-8 sm:w-8 hover:bg-black/5 ${isRunning || uploading ? 'pointer-events-none opacity-30' : 'cursor-pointer'}`} aria-label="添加附件"><Paperclip className={`h-4 w-4 ${uploading ? 'animate-pulse' : ''}`} /><input type="file" multiple disabled={isRunning || uploading} className="hidden" onChange={(event) => void uploadAttachments(event)} /></label> : null}{isRunning && activeTurnId ? <button type="button" onClick={() => void api.interruptWorkspaceTurn(activeTurnId)} className="grid h-11 w-11 place-items-center rounded-full text-red-500 sm:h-8 sm:w-8 hover:bg-red-50" aria-label="停止"><Square className="h-3.5 w-3.5 fill-current" /></button> : <button type="button" onClick={() => void send()} disabled={!text.trim() || isRunning || uploading || waiting !== null} className="grid h-11 w-11 place-items-center rounded-full bg-black text-white sm:h-8 sm:w-8 hover:bg-black/85 disabled:opacity-30" aria-label="发送"><Send className="h-4 w-4" /></button>}</div></div></div></div><SessionToolsDialog open={toolsOpen} onClose={() => setToolsOpen(false)} workspace={workspace} session={session} models={models} model={model} onModel={setModel} /></div>;
}

function SessionActionsMenu({ session, busy, onNew, onRename, onDelete, onAdvanced, onAction, onAttach, uploading, attachDisabled }: { onAttach?(): void; uploading?: boolean; attachDisabled?: boolean; session: WorkspaceSession; busy: boolean; onNew(): void; onRename(): void; onDelete(): void; onAdvanced(): void; onAction(kind: 'compact' | 'review' | 'archive'): void }) {
  const hasThread = Boolean(session.thread_id);
  const itemClass = (focus: boolean, danger = false) => `w-full rounded-xl px-3 py-3 sm:py-2 text-left text-sm ${danger ? 'text-red-600' : 'text-black/70'} ${focus ? (danger ? 'bg-red-50' : 'bg-black/5') : ''}`;
  return (
    <Menu as="div" className="relative shrink-0">
      <MenuButton type="button" className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-black/40 sm:h-9 sm:w-9 hover:bg-black/5 data-[open]:bg-black/5" aria-label="会话菜单">{onAttach ? <PlusIcon /> : <MoreHorizontal className="h-4 w-4" />}</MenuButton>
      <MenuItems anchor={onAttach ? 'top start' : 'bottom end'} className="z-[75] my-2 max-h-[var(--anchor-max-height)] w-48 overflow-y-auto rounded-2xl bg-white p-1 shadow-[0_20px_70px_rgba(0,0,0,0.18)] ring-1 ring-black/5 focus:outline-none">
        {onAttach ? <MenuItem disabled={attachDisabled}>{({ focus }) => <button type="button" disabled={attachDisabled} onClick={onAttach} className={`${itemClass(focus)} disabled:opacity-35`}>{uploading ? '上传中…' : '添加附件'}</button>}</MenuItem> : null}
        <MenuItem>{({ focus }) => <button type="button" onClick={onNew} className={itemClass(focus)}>新建会话</button>}</MenuItem>
        <MenuItem>{({ focus }) => <button type="button" onClick={onRename} className={itemClass(focus)}>重命名</button>}</MenuItem>
        <div className="my-1 border-t border-black/[0.06]" />
        <MenuItem disabled={!hasThread || busy}>{({ focus, disabled }) => <button type="button" disabled={disabled} onClick={() => onAction('compact')} className={`${itemClass(focus)} disabled:opacity-35`}>压缩上下文</button>}</MenuItem>
        <MenuItem disabled={!hasThread || busy}>{({ focus, disabled }) => <button type="button" disabled={disabled} onClick={() => onAction('review')} className={`${itemClass(focus)} disabled:opacity-35`}>启动 Review</button>}</MenuItem>
        <MenuItem disabled={!hasThread || busy}>{({ focus, disabled }) => <button type="button" disabled={disabled} onClick={() => onAction('archive')} className={`${itemClass(focus)} disabled:opacity-35`}>归档 Session</button>}</MenuItem>
        <MenuItem>{({ focus }) => <button type="button" onClick={onAdvanced} className={itemClass(focus)}>高级设置</button>}</MenuItem>
        <div className="my-1 border-t border-black/[0.06]" />
        <MenuItem>{({ focus }) => <button type="button" onClick={onDelete} className={itemClass(focus, true)}>删除会话</button>}</MenuItem>
      </MenuItems>
    </Menu>
  );
}

function workspaceWaitingRequest(event: WorkspaceEvent): RunWaitingRequest | null {
  if (!event.event_type.toLowerCase().includes('waiting') && !event.event_type.toLowerCase().includes('requestuserinput') && event.event_type !== 'decision_request') return null;
  const candidate = (event.payload.request ?? event.payload) as Partial<RunWaitingRequest>;
  if (!candidate.context || !Array.isArray(candidate.groups) || typeof candidate.request_id !== 'string') return null;
  return candidate as RunWaitingRequest;
}

function hasWorkspaceWaitingRequest(events: WorkspaceEvent[]): boolean {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event_type === 'decision_submitted') return false;
    if (workspaceWaitingRequest(event)) return true;
    if (event.event_type === 'turn_completed' || event.event_type === 'error') return false;
  }
  return false;
}

function SessionToolsDialog({ open, onClose, workspace, session, models, model, onModel }: { open: boolean; onClose(): void; workspace: Workspace; session: WorkspaceSession; models: string[]; model: string; onModel(value: string): void }) {
  const [processes, setProcesses] = useState<Array<{ process_id: string; status: string; duration_ms?: number }>>([]);
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    setProcesses(await api.getWorkspaceProcesses(session.id));
  };
  useEffect(() => { if (open && session.thread_id) void refresh().catch(() => undefined); }, [open, session.id, session.thread_id]);
  const clean = async () => {
    setBusy(true);
    try { await api.cleanWorkspaceProcesses(session.id); await refresh(); } finally { setBusy(false); }
  };
  return <AppDialog open={open} onClose={onClose} title="高级设置" widthClassName="max-w-xl"><div className="space-y-4"><div className="grid grid-cols-2 gap-2 text-xs"><div className="rounded-2xl bg-[#F4F5F7] p-3"><div className="text-black/40">运行环境</div><div className="mt-1 font-medium">{workspaceRuntimeLabel(workspace.runtime_status)}</div></div><div className="rounded-2xl bg-[#F4F5F7] p-3"><div className="text-black/40">会话状态</div><div className="mt-1 font-medium">{workspaceSessionLabel(session.status)}</div></div></div><label className="block text-xs font-medium text-black/55">下一轮模型<select value={model} onChange={(event) => onModel(event.target.value)} className="mt-2 h-10 w-full rounded-xl border border-black/10 bg-white px-3 text-sm"><option value="">使用默认模型</option>{models.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>{processes.length ? <div className="rounded-2xl border border-black/5 p-3"><div className="flex items-center justify-between"><div className="text-xs font-semibold text-black/55">后台进程</div><div className="flex gap-1"><button type="button" disabled={busy} onClick={() => void refresh()} className="rounded-full px-2 py-1 text-[11px] text-black/45 hover:bg-black/5 disabled:opacity-35">刷新</button><button type="button" disabled={busy} onClick={() => void clean()} className="rounded-full px-2 py-1 text-[11px] text-black/45 hover:bg-black/5 disabled:opacity-35">清理</button></div></div><div className="mt-2 space-y-1.5">{processes.map((process) => <div key={process.process_id} className="flex items-center gap-2 rounded-xl bg-[#F4F5F7] px-3 py-2 text-xs"><span className="min-w-0 flex-1 truncate">进程 {process.process_id}</span><span className="text-black/40">{process.status}{process.duration_ms != null ? ` · ${(process.duration_ms / 1000).toFixed(1)}s` : ''}</span><button type="button" onClick={() => void api.stopWorkspaceProcess(session.id, process.process_id).then(refresh)} className="text-red-500">停止</button></div>)}</div></div> : null}</div></AppDialog>;
}

function workspaceRuntimeLabel(status: Workspace['runtime_status']) {
  return ({ stopped: '已停止', starting: '启动中', ready: '可用', busy: '忙碌', error: '异常' } as const)[status];
}

function workspaceSessionLabel(status: WorkspaceSession['status']) {
  return ({ idle: '空闲', running: '处理中', waiting: '等待回复', error: '异常' } as const)[status];
}

function collapseWorkspaceEvents(events: WorkspaceEvent[]): WorkspaceEvent[] {
  const visible: WorkspaceEvent[] = [];
  let pending: WorkspaceEvent | null = null;
  for (const event of events) {
    if (event.event_type === 'message_delta') {
      if (pending && pending.turn_id !== event.turn_id) visible.push(pending);
      const previousText = pending && pending.turn_id === event.turn_id ? String(pending.payload.text ?? '') : '';
      const text: string = `${previousText}${String(event.payload.text ?? '')}`;
      pending = { ...event, payload: { ...event.payload, text } };
      continue;
    }
    if (event.event_type === 'message_completed') {
      pending = null;
      visible.push(event);
      continue;
    }
    if (event.event_type.startsWith('workflow_run_') && typeof event.payload.run_id === 'string') {
      const existingIndex = visible.findIndex((item) => item.event_type.startsWith('workflow_run_') && item.payload.run_id === event.payload.run_id);
      if (existingIndex >= 0) visible[existingIndex] = event;
      else visible.push(event);
      continue;
    }
    if (pending && (event.event_type === 'turn_completed' || event.event_type === 'error')) {
      visible.push(pending);
      pending = null;
    }
    visible.push(event);
  }
  if (pending) visible.push(pending);
  return visible;
}

function groupWorkspaceEvents(events: WorkspaceEvent[]): Array<{ key: string; events: WorkspaceEvent[] }> {
  const groups: Array<{ key: string; events: WorkspaceEvent[] }> = [];
  for (const event of collapseWorkspaceEvents(events)) {
    const key = event.turn_id ?? `event-${event.id}`;
    const current = groups.at(-1);
    if (current?.key === key) current.events.push(event);
    else groups.push({ key, events: [event] });
  }
  return groups;
}

function EventBubble({ flat = false, event, workspaceId, workflowRun, onOpenWorkflowRun }: { flat?: boolean; event: WorkspaceEvent; workspaceId: string; workflowRun?: WorkspaceWorkflowRun; onOpenWorkflowRun(runId: string): void }) {
  const payload = event.payload ?? {};
  if (event.event_type.startsWith('workflow_run_')) {
    const name = String(payload.app_name ?? '工作流');
    const status = event.event_type === 'workflow_run_started'
      ? '运行中'
      : event.event_type === 'workflow_run_waiting'
        ? '等待回复'
        : workflowRunStatusLabel(String(payload.status ?? workflowRun?.status ?? 'failed'));
    const runId = typeof payload.run_id === 'string' ? payload.run_id : workflowRun?.run_id;
    return <div className="flex justify-start"><button type="button" disabled={!runId} onClick={() => runId && onOpenWorkflowRun(runId)} className="flex max-w-[88%] items-center gap-2 rounded-xl border border-black/5 bg-white px-3 py-2 text-left text-xs text-black/55 shadow-sm hover:border-black/15 disabled:cursor-default"><GitBranch className="h-3.5 w-3.5 shrink-0" /><span className="min-w-0 flex-1 truncate">{name}</span><span className="shrink-0 text-black/35">{status}</span>{runId ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-black/25" /> : null}</button></div>;
  }
  const text = String(payload.text ?? payload.content ?? payload.delta ?? payload.message ?? '');
  const role = String(payload.role ?? payload.author ?? 'assistant');
  const messageAttachments = workspaceMessageAttachments(payload.attachments, event.turn_id);
  if (!text && messageAttachments.length === 0 && event.event_type !== 'status') return null;
  const dark = role === 'user';
  return <div className={`flex ${dark ? 'justify-end' : 'justify-start'}`}>
    <div className="min-w-0 max-w-[95%] space-y-2 sm:max-w-[88%]">
      {text ? <div className={`whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2.5 text-sm leading-6 [overflow-wrap:anywhere] ${dark ? (flat ? 'bg-[#F1F2F4] text-black/85' : 'bg-black text-white') : flat ? 'text-black/75' : 'bg-white text-black/75 shadow-sm'}`}>{text}</div> : null}
      {messageAttachments.length ? <div className={`flex flex-wrap gap-2 ${dark ? 'justify-end' : ''}`}>{messageAttachments.map((attachment, index) => <WorkspaceMessageAttachment key={attachment.id || attachment.path || `${attachment.name}-${index}`} attachment={attachment} workspaceId={workspaceId} />)}</div> : null}
    </div>
  </div>;
}

interface WorkspaceMessageAttachmentValue {
  id?: string;
  name: string;
  mime?: string;
  path?: string;
}

function workspaceMessageAttachments(value: unknown, turnId?: string | null): WorkspaceMessageAttachmentValue[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap<WorkspaceMessageAttachmentValue>((item): WorkspaceMessageAttachmentValue[] => {
    if (typeof item === 'string' && item.trim()) return [{ name: item.trim(), path: turnId ? `inputs/${turnId}/${item.trim()}` : undefined }];
    if (!item || typeof item !== 'object') return [];
    const record = item as Record<string, unknown>;
    const name = typeof record.name === 'string' ? record.name.trim() : '';
    if (!name) return [];
    return [{ id: typeof record.id === 'string' ? record.id : undefined, name, mime: typeof record.mime === 'string' ? record.mime : undefined }];
  });
}

function WorkspaceMessageAttachment({ attachment, workspaceId }: { attachment: WorkspaceMessageAttachmentValue; workspaceId: string }) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const isImage = Boolean((attachment.id || attachment.path) && (attachment.mime?.startsWith('image/') || /\.(?:png|jpe?g|gif|webp)$/i.test(attachment.name)));
  useEffect(() => {
    if (!isImage || (!attachment.id && !attachment.path)) return;
    const controller = new AbortController();
    let closed = false;
    let objectUrl: string | null = null;
    const image = attachment.id
      ? api.fetchUploadBlob(attachment.id, controller.signal)
      : api.downloadWorkspaceFile(workspaceId, attachment.path as string);
    void image.then((blob) => {
      if (closed) return;
      objectUrl = URL.createObjectURL(blob);
      setImageUrl(objectUrl);
    }).catch(() => setImageUrl(null));
    return () => {
      closed = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment.id, attachment.path, isImage, workspaceId]);
  if (imageUrl) return <figure className="w-48 max-w-full overflow-hidden rounded-xl border border-black/10 bg-white"><WorkspaceImage src={imageUrl} name={attachment.name} className="max-h-60 w-full object-contain" /><figcaption className="truncate border-t border-black/5 px-2 py-1.5 text-[11px] leading-4 text-black/55">{attachment.name}</figcaption></figure>;
  return <div className="inline-flex max-w-56 items-center gap-1.5 rounded-xl bg-white px-2.5 py-1.5 text-xs leading-5 text-black/60"><File className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{attachment.name}</span></div>;
}

async function downloadWorkspacePath(workspaceId: string, path: string, name: string) {
  const blob = await api.downloadWorkspaceFile(workspaceId, path);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function WorkspaceRunImage({ workspaceId, file }: { workspaceId: string; file: WorkspaceWorkflowRun['files'][number] }) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  useEffect(() => {
    let closed = false;
    let objectUrl: string | null = null;
    void api.downloadWorkspaceFile(workspaceId, file.path).then((blob) => {
      if (closed) return;
      objectUrl = URL.createObjectURL(blob);
      setImageUrl(objectUrl);
    }).catch(() => setImageUrl(null));
    return () => {
      closed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file.path, workspaceId]);
  return <figure className="group relative overflow-hidden rounded-xl border border-black/10 bg-[#F4F5F7]"><div className="flex aspect-square items-center justify-center">{imageUrl ? <WorkspaceImage src={imageUrl} name={file.name} className="aspect-square w-full object-contain" /> : <File className="h-5 w-5 text-black/20" />}</div><figcaption className="flex items-center gap-1.5 border-t border-black/10 bg-white px-2 py-1.5"><span className="min-w-0 flex-1 truncate text-[11px] text-black/55">{file.name}</span><button type="button" onClick={() => void downloadWorkspacePath(workspaceId, file.path, file.name)} className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-black/40 hover:bg-black/5 hover:text-black" aria-label={`下载 ${file.name}`}><Download className="h-3.5 w-3.5" /></button></figcaption></figure>;
}

function WorkspaceFiles({ workspace, files, onRefresh, onPreview }: { workspace: Workspace; files: WorkspaceFile[]; onRefresh(): void; onPreview(file: WorkspaceFile): void }) {
  const [uploading, setUploading] = useState(false);
  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []);
    if (!selected.length) return;
    setUploading(true);
    try { await api.uploadWorkspaceFiles(workspace.id, selected); onRefresh(); }
    finally { setUploading(false); event.target.value = ''; }
  };
  return <div className="mx-auto max-w-6xl">
    <div className="mb-6 flex items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">项目文件</h2><p className="mt-1 text-xs text-black/45">{files.filter((file) => file.kind === 'file').length} 个文件</p></div><label className={`inline-flex h-10 items-center gap-2 rounded-lg bg-black px-4 text-sm font-medium text-white ${uploading ? 'cursor-wait opacity-50' : 'cursor-pointer hover:bg-black/85'}`}><Upload className="h-4 w-4" />{uploading ? '上传中…' : '上传文件'}<input type="file" multiple disabled={uploading} className="hidden" onChange={(event) => void upload(event)} /></label></div>
    <div className="overflow-hidden rounded-xl border border-black/[0.08]">
      <div className="flex items-center gap-3 border-b border-black/[0.08] bg-[#F7F8FA] px-4 py-2.5 text-xs font-medium text-black/45"><span className="flex-1">文件名称</span><span className="hidden w-24 sm:block">类型</span><span className="w-20 text-right">大小</span><span className="w-4" /></div>
      {files.length ? files.map((file) => <button type="button" key={file.path} disabled={file.kind === 'directory'} onClick={() => onPreview(file)} className="flex w-full items-center gap-3 border-b border-black/[0.06] px-4 py-3 text-left last:border-b-0 enabled:hover:bg-black/[0.02] disabled:cursor-default"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[#F4F5F7]">{file.kind === 'directory' ? <Folder className="h-4 w-4 text-black/45" /> : <File className="h-4 w-4 text-black/45" />}</span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-black/75" title={file.name}>{file.name}</span>{file.path !== file.name ? <span className="mt-0.5 block truncate text-xs text-black/40" title={file.path}>{file.path}</span> : null}</span><span className="hidden w-24 truncate text-xs text-black/45 sm:block">{file.kind === 'directory' ? '文件夹' : file.name.includes('.') ? file.name.split('.').pop()?.toUpperCase() : '文件'}</span><span className="w-20 shrink-0 text-right text-xs tabular-nums text-black/45">{file.kind === 'file' ? formatSize(file.size) : '—'}</span><span className="w-4 shrink-0">{file.kind === 'file' ? <ChevronRight className="h-4 w-4 text-black/25" /> : null}</span></button>) : <div className="px-4 py-16 text-center text-sm text-black/40">目录为空</div>}
    </div>
  </div>;
}

function FilePreviewDialog({ workspace, file, onClose }: { workspace: Workspace; file: WorkspaceFile | null; onClose(): void }) {
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.previewWorkspaceFile>> | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setPreview(null);
    setImageUrl(null);
    setFailed(false);
    if (!file) return;
    let closed = false;
    let objectUrl: string | null = null;
    void api.previewWorkspaceFile(workspace.id, file.path).then(async (result) => {
      if (closed) return;
      setPreview(result);
      if (result.mime.startsWith('image/')) {
        const blob = await api.downloadWorkspaceFile(workspace.id, file.path);
        if (closed) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      }
    }).catch(() => { if (!closed) setFailed(true); });
    return () => { closed = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [file, workspace.id]);
  return <AppDialog open={file !== null} onClose={onClose} title={file?.name ?? '文件预览'} description={<span className="break-all">{file?.path}</span>} widthClassName="max-w-3xl" footer={file ? <button type="button" onClick={() => void downloadWorkspacePath(workspace.id, file.path, file.name)} className="inline-flex items-center gap-1.5 rounded-full bg-black px-4 py-2 text-sm font-medium text-white"><Download className="h-4 w-4" />下载</button> : undefined}>
    {failed ? <div className="py-12 text-center text-sm text-black/45">无法加载预览，请重新打开或下载文件。</div> : imageUrl ? <WorkspaceImage src={imageUrl} name={file?.name ?? '图片'} className="max-h-[60dvh] w-full object-contain" /> : preview && !preview.mime.startsWith('image/') ? <div className="max-h-[60dvh] overflow-auto rounded-2xl bg-[#F4F5F7] p-4">{preview.content != null ? <pre className="whitespace-pre-wrap break-words text-xs leading-6 text-black/75">{preview.content}</pre> : <div className="text-sm text-black/45">此文件类型仅支持下载。</div>}</div> : <div className="py-12 text-center text-sm text-black/40">加载预览…</div>}
  </AppDialog>;
}

function WorkspaceWorkflow({ workspace, proposals, runs, selectedRunId, onSelectedRunHandled, onReturnToChat, onRefresh }: { workspace: Workspace; proposals: WorkspaceWorkflowProposal[]; runs: WorkspaceWorkflowRun[]; selectedRunId: string | null; onSelectedRunHandled(): void; onReturnToChat(run: WorkspaceWorkflowRun, repair: boolean): void; onRefresh(): void }) {
  const [selected, setSelected] = useState<WorkspaceWorkflowProposal | null>(null);
  const [selectedRun, setSelectedRun] = useState<WorkspaceWorkflowRun | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!selectedRunId) return;
    const run = runs.find((item) => item.run_id === selectedRunId);
    if (run) setSelectedRun(run);
    onSelectedRunHandled();
  }, [onSelectedRunHandled, runs, selectedRunId]);
  const confirm = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await api.confirmWorkspaceWorkflowProposal(workspace.id, selected.id);
      setSelected(null);
      onRefresh();
    } finally {
      setBusy(false);
    }
  };
  const graphNodes = useMemo(() => selected?.graph.nodes.map((node) => ({
    id: node.id,
    position: node.position,
    data: { label: node.title },
  })) ?? [], [selected]);
  const graphEdges = useMemo(() => selected?.graph.execution_edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.branch_key,
  })) ?? [], [selected]);
  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-6"><h2 className="text-lg font-semibold">工作流</h2><p className="mt-1 text-xs text-black/45">{proposals.filter((item) => item.status === 'pending').length} 个待确认提案 · {runs.length} 条运行记录</p></div>
      <div className="mb-6 grid items-start gap-6 xl:grid-cols-2">
        <section className="overflow-hidden rounded-xl border border-black/[0.08]"><h3 className="border-b border-black/[0.08] bg-[#F7F8FA] px-4 py-3 text-xs font-semibold text-black/55">工作流提案</h3>{proposals.length ? proposals.map((proposal) => <button type="button" key={proposal.id} onClick={() => setSelected(proposal)} className="flex w-full items-center gap-3 border-b border-black/[0.06] px-4 py-4 text-left last:border-b-0 hover:bg-black/[0.02]"><GitBranch className="h-4 w-4 shrink-0 text-black/45" /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium" title={proposal.name}>{proposal.name}</span><span className="mt-1 block text-xs text-black/40">{proposal.kind === 'create' ? '新建应用' : '修改应用'}</span></span><span className={`shrink-0 rounded-md px-2 py-1 text-xs ${proposal.status === 'pending' ? 'bg-amber-50 text-amber-700' : 'bg-black/[0.04] text-black/50'}`}>{workflowProposalStatusLabel(proposal.status)}</span><ChevronRight className="h-4 w-4 shrink-0 text-black/25" /></button>) : <div className="px-4 py-10 text-center text-sm text-black/40">暂无工作流提案</div>}</section>
        <section className="overflow-hidden rounded-xl border border-black/[0.08]"><h3 className="border-b border-black/[0.08] bg-[#F7F8FA] px-4 py-3 text-xs font-semibold text-black/55">运行记录</h3>{runs.length ? runs.map((run) => <button type="button" key={run.run_id} onClick={() => setSelectedRun(run)} className="flex w-full items-center gap-3 border-b border-black/[0.06] px-4 py-4 text-left last:border-b-0 hover:bg-black/[0.02]"><span className={`h-2 w-2 shrink-0 rounded-full ${run.status === 'success' ? 'bg-emerald-500' : run.status === 'failed' ? 'bg-red-500' : 'bg-amber-500'}`} /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-black/75" title={run.app_name}>{run.app_name}</span>{run.finished_at ? <span className="mt-1 block text-xs text-black/40">{new Date(run.finished_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</span> : null}</span><span className="shrink-0 text-xs text-black/50">{workflowRunStatusLabel(run.status)}</span><ChevronRight className="h-4 w-4 shrink-0 text-black/25" /></button>) : <div className="px-4 py-10 text-center text-sm text-black/40">暂无运行记录</div>}</section>
      </div>
      <AppDialog open={selected !== null} onClose={() => !busy && setSelected(null)} title={selected?.name ?? '工作流提案'} description={selected?.description} widthClassName="max-w-4xl" footer={selected?.status === 'pending' ? <><button type="button" onClick={() => { if (selected) void api.rejectWorkspaceWorkflowProposal(workspace.id, selected.id).then(() => { setSelected(null); onRefresh(); }); }} className="rounded-full border border-black/10 px-4 py-2 text-sm">拒绝</button><button type="button" disabled={busy} onClick={() => void confirm()} className="inline-flex items-center gap-1.5 rounded-full bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40"><Check className="h-4 w-4" />确认应用</button></> : undefined}>
        {selected ? <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]"><div className="rounded-2xl bg-[#F4F5F7] p-4"><div className="text-xs font-semibold text-black/55">工作流预览</div><div className="mt-3 h-[360px] overflow-hidden rounded-xl border border-black/10 bg-white"><ReactFlow nodes={graphNodes} edges={graphEdges} fitView minZoom={0.1} fitViewOptions={{ padding: 0.2 }} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} zoomOnScroll={false} proOptions={{ hideAttribution: true }}><Background color="#e5e7eb" gap={20} /><Controls showInteractive={false} /></ReactFlow></div></div><div className="rounded-2xl border border-black/5 bg-white p-4"><div className="text-xs font-semibold text-black/55">工作流检查</div><div className={`mt-2 text-sm ${selected.lint.ok ? 'text-emerald-600' : 'text-red-600'}`}>{selected.lint.ok ? '通过' : '需要修正'}</div>{selected.lint.issues.map((issue, index) => <div key={index} className="mt-2 text-xs leading-5 text-black/50">{issue.detail}</div>)}</div></div> : null}
      </AppDialog>
      <WorkspaceWorkflowRunDialog workspace={workspace} run={selectedRun} onClose={() => setSelectedRun(null)} onReturnToChat={(repair) => { if (selectedRun) onReturnToChat(selectedRun, repair); setSelectedRun(null); }} />
    </div>
  );
}

function WorkspaceWorkflowRunDialog({ workspace, run, onClose, onReturnToChat }: { workspace: Workspace; run: WorkspaceWorkflowRun | null; onClose(): void; onReturnToChat(repair: boolean): void }) {
  const [resultHtml, setResultHtml] = useState<string | null>(null);
  const [resultLoading, setResultLoading] = useState(false);
  useEffect(() => {
    setResultHtml(null);
    if (!run?.result_path) return;
    let closed = false;
    setResultLoading(true);
    void api.previewWorkspaceFile(workspace.id, run.result_path).then((preview) => {
      if (!closed) setResultHtml(preview.content ?? null);
    }).catch(() => {
      if (!closed) setResultHtml(null);
    }).finally(() => {
      if (!closed) setResultLoading(false);
    });
    return () => { closed = true; };
  }, [run?.result_path, workspace.id]);
  if (!run) return null;
  const images = run.files.filter((file) => file.mime.startsWith('image/'));
  const otherFiles = run.files.filter((file) => !file.mime.startsWith('image/'));
  const needsConversation = ['failed', 'cancelled', 'interrupted', 'waiting_for_user'].includes(run.status);
  const footer = needsConversation ? <><button type="button" onClick={() => onReturnToChat(false)} className="rounded-full border border-black/10 px-4 py-2 text-sm">{run.status === 'waiting_for_user' ? '回复 Codex' : '返回对话'}</button>{run.status === 'failed' ? <button type="button" onClick={() => onReturnToChat(true)} className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white">让 Codex 修复</button> : null}</> : undefined;
  return <AppDialog open onClose={onClose} title={run.app_name} description={`${workflowRunStatusLabel(run.status)}${run.finished_at ? ` · ${run.finished_at}` : ''}`} widthClassName="max-w-4xl" footer={footer}><div className="space-y-4">{run.error ? <div className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-600">{run.error}</div> : null}{run.result_path ? <section><div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-semibold text-black/55">运行结果</h3><button type="button" onClick={() => void downloadWorkspacePath(workspace.id, run.result_path as string, 'result.html')} className="inline-flex items-center gap-1 rounded-full px-2.5 py-1.5 text-xs text-black/45 hover:bg-black/5 hover:text-black"><Download className="h-3.5 w-3.5" />下载</button></div><div className="overflow-hidden rounded-2xl border border-black/10 bg-[#F4F5F7]">{resultHtml ? <HtmlOutputPreview title={`${run.app_name} 运行结果`} html={resultHtml} /> : <div className="grid h-40 place-items-center text-sm text-black/40">{resultLoading ? '加载预览…' : '无法预览'}</div>}</div></section> : null}{images.length ? <section><h3 className="mb-2 text-xs font-semibold text-black/55">图片</h3><div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">{images.map((file) => <WorkspaceRunImage key={file.path} workspaceId={workspace.id} file={file} />)}</div></section> : null}{otherFiles.length ? <section><h3 className="mb-2 text-xs font-semibold text-black/55">文件</h3><div className="divide-y divide-black/5 rounded-2xl border border-black/5">{otherFiles.map((file) => <button type="button" key={file.path} onClick={() => void downloadWorkspacePath(workspace.id, file.path, file.name)} className="flex w-full items-center gap-3 px-3.5 py-3 text-left hover:bg-black/[0.02]"><File className="h-4 w-4 shrink-0 text-black/35" /><span className="min-w-0 flex-1 truncate text-sm text-black/70">{file.name}</span><span className="text-[11px] text-black/35">{formatSize(file.size)}</span><Download className="h-4 w-4 text-black/25" /></button>)}</div></section> : null}</div></AppDialog>;
}

function WorkspaceSettingsDialog({ open, workspace, onClose }: { open: boolean; workspace: Workspace; onClose(): void }) {
  return <AppDialog open={open} onClose={onClose} title="工作空间设置" widthClassName="max-w-xl"><WorkspaceGit workspace={workspace} /></AppDialog>;
}

function WorkspaceGit({ workspace }: { workspace: Workspace }) {
  const [config, setConfig] = useState<Awaited<ReturnType<typeof api.getWorkspaceGitConfig>> | null>(null);
  const [repositoryUrl, setRepositoryUrl] = useState('');
  const [branch, setBranch] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void api.getWorkspaceGitConfig(workspace.id).then((next) => {
      setConfig(next);
      setRepositoryUrl(next.repository_url ?? '');
      setBranch(next.default_branch ?? '');
    }).catch(() => setConfig(null));
  }, [workspace.id]);
  if (!config) return <div className="py-12 text-center text-sm text-black/40">加载 Git 状态…</div>;
  const save = async () => {
    setBusy(true);
    try {
      const next = await api.saveWorkspaceGitConfig(workspace.id, { repository_url: repositoryUrl.trim(), default_branch: branch.trim() || undefined, access_token: token || undefined });
      setConfig(next);
      setToken('');
    } finally { setBusy(false); }
  };
  return <div className="space-y-5">
    <div className="flex items-center gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl bg-[#F4F5F7]"><GitBranch className="h-5 w-5 text-black/60" /></span><div><h2 className="text-sm font-semibold">Git 仓库</h2><p className="mt-1 text-xs text-black/45">{config.token_configured ? '访问令牌已配置' : '尚未配置访问令牌'}</p></div></div>
    <label className="block text-xs font-medium text-black/60">HTTPS 仓库地址<input value={repositoryUrl} onChange={(event) => setRepositoryUrl(event.target.value)} placeholder="https://git.example.com/team/project.git" className="mt-2 h-11 w-full rounded-lg border border-black/15 bg-white px-3 text-sm outline-none focus:border-black/40" /></label>
    <div className="grid gap-4 sm:grid-cols-2"><label className="min-w-0 text-xs font-medium text-black/60">默认分支<input value={branch} onChange={(event) => setBranch(event.target.value)} placeholder="main" className="mt-2 h-11 w-full rounded-lg border border-black/15 bg-white px-3 text-sm outline-none focus:border-black/40" /></label><label className="min-w-0 text-xs font-medium text-black/60">访问令牌<input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" placeholder={config.token_configured ? '留空则保留现有令牌' : '可选'} className="mt-2 h-11 w-full rounded-lg border border-black/15 bg-white px-3 text-sm outline-none focus:border-black/40" /></label></div>
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-black/[0.08] pt-4"><span className="min-w-0 break-words text-xs text-black/45">允许的主机：{config.allowed_hosts.join('、') || '管理员尚未配置'}</span><button type="button" disabled={busy || !repositoryUrl.trim()} onClick={() => void save()} className="h-10 shrink-0 rounded-lg bg-black px-4 text-sm font-medium text-white disabled:opacity-40">{busy ? '保存中…' : '保存配置'}</button></div>
  </div>;
}

function workflowRunStatusLabel(status: string) {
  return ({ pending: '准备中', running: '运行中', waiting_for_user: '等待回复', success: '已完成', failed: '失败', cancelled: '已停止', interrupted: '已中断' } as Record<string, string>)[status] ?? status;
}

function workflowProposalStatusLabel(status: WorkspaceWorkflowProposal['status']) {
  return ({ pending: '待确认', applied: '已应用', stale: '已过期', rejected: '已拒绝' } as const)[status];
}

function workflowRepairPrompt(run: WorkspaceWorkflowRun): string {
  const error = run.error?.trim() ? `\n错误：${run.error.trim()}` : '';
  return `请检查刚才失败的工作流「${run.app_name}」，定位根因并修复后重新执行。${error}`;
}

function NewSessionDialog({ open, onClose, onCreate }: { open: boolean; onClose(): void; onCreate(title: string): Promise<void> }) { const [title, setTitle] = useState(''); return <AppDialog open={open} onClose={onClose} title="新建会话" footer={<><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-4 py-2 text-sm">取消</button><button type="button" disabled={!title.trim()} onClick={() => void onCreate(title.trim())} className="rounded-full bg-black px-4 py-2 text-sm text-white disabled:opacity-40">创建</button></>}><input value={title} onChange={(event) => setTitle(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && title.trim()) void onCreate(title.trim()); }} aria-label="会话名称" autoFocus placeholder="例如：重构登录页" className="h-11 w-full rounded-xl border border-black/10 px-3 text-sm outline-none focus:border-black/30" /></AppDialog>; }

function PlusIcon() { return <span className="text-lg leading-none">+</span>; }
function formatSize(size: number) { if (size < 1024) return `${size} B`; if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`; return `${(size / 1024 / 1024).toFixed(1)} MB`; }
