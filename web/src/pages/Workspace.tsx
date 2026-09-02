import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Bot, Check, ChevronRight, Download, File, Folder, GitBranch, MessageSquare, MoreHorizontal, Paperclip, RefreshCw, Search, Send, Square, Upload, X } from 'lucide-react';
import { useNavigate, useParams } from 'react-router-dom';
import { Background, Controls, ReactFlow } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { TopNav } from '../components/common/TopNav';
import { ConfirmDialog } from '../components/common/ConfirmDialog';
import { AppDialog } from '../components/common/AppDialog';
import { useWorkspaceStore } from '../stores/useWorkspaceStore';
import { showCaughtError } from '../stores/useErrorDialogStore';
import * as api from '../lib/api';
import type { Workspace, WorkspaceEvent, WorkspaceFile, WorkspaceSession, WorkspaceWorkflowProposal, WorkspaceWorkflowRun } from '../types';
import type { DecisionAnswer, RunWaitingRequest, UploadRef } from '../types';
import { DecisionPromptPanel } from '../components/common/DecisionPromptPanel';
import { PromptDialog } from '../components/common/PromptDialog';

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
  const [tab, setTab] = useState<'chat' | 'files' | 'workflow' | 'git'>('chat');
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
        if (next.some((item) => item.event_type === 'workflow_run_finished')) {
          void api.listWorkspaceWorkflowRuns(workspace.id).then(setWorkflowRuns);
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

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-[#F4F5F7] text-[#0B0B0F]">
      {!mobile ? <TopNav /> : null}
      <main className={`flex min-h-0 w-full flex-1 flex-col overflow-hidden ${mobile ? 'px-4 pb-4 pt-4' : 'mx-auto max-w-7xl px-6 pb-6 pt-6'}`}>
        <div className="mb-5 flex shrink-0 items-center gap-3">
          <button type="button" onClick={onBack} className="grid h-9 w-9 place-items-center rounded-full border border-black/10 bg-white text-black/55 hover:text-black" aria-label="返回"><ArrowLeft className="h-4 w-4" /></button>
          <div className="min-w-0 flex-1"><h1 className="truncate text-2xl font-semibold tracking-tight">{workspace.name}</h1>{workspace.description ? <p className="mt-0.5 truncate text-xs text-black/45">{workspace.description}</p> : null}</div>
          <button type="button" onClick={() => void syncWiki()} disabled={syncingWiki} aria-busy={syncingWiki} className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-black/10 bg-white px-3 text-xs font-medium text-black/55 hover:text-black disabled:cursor-wait disabled:opacity-55"><RefreshCw className={`h-3.5 w-3.5 ${syncingWiki ? 'animate-spin' : ''}`} />{syncingWiki ? '同步中…' : '同步 Wiki'}</button>
        </div>
        <div className={`${mobile ? 'flex min-h-0 flex-1 flex-col gap-4' : 'grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)] gap-5'}`}>
          <aside className={`rounded-[22px] border border-black/5 bg-white p-3 shadow-card ${mobile ? 'max-h-40 shrink-0 overflow-y-auto' : 'min-h-0 overflow-y-auto'}`}>
            <div className="mb-2 flex items-center justify-between px-2"><span className="text-xs font-semibold uppercase tracking-wider text-black/45">会话</span><div className="flex items-center gap-1"><button type="button" onClick={() => setSessionSearchOpen(true)} className="grid h-7 w-7 place-items-center rounded-full hover:bg-black/5" aria-label="搜索会话"><Search className="h-3.5 w-3.5" /></button><button type="button" onClick={() => setNewSessionOpen(true)} className="grid h-7 w-7 place-items-center rounded-full hover:bg-black/5" aria-label="新建会话"><PlusIcon /></button></div></div>
            <div className="space-y-1">{sessions.map((item) => <button type="button" key={item.id} onClick={() => setSession(item)} className={`flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm ${session?.id === item.id ? 'bg-black text-white' : 'text-black/65 hover:bg-black/5'}`}><MessageSquare className="h-3.5 w-3.5 shrink-0" /><span className="min-w-0 flex-1 truncate">{item.title}</span>{item.status === 'running' ? <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-400" /> : null}</button>)}{sessionHasMore ? <button type="button" onClick={async () => { const page = await api.listWorkspaceSessions(workspace.id, { offset: sessionOffset }); setSessions((current) => [...current, ...page.items]); setSessionHasMore(page.has_more); setSessionOffset(page.next_offset ?? sessionOffset + page.items.length); }} className="w-full rounded-xl px-3 py-2 text-xs text-black/40 hover:bg-black/5">加载更多</button> : null}</div>
          </aside>
          <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[22px] border border-black/5 bg-white shadow-card">
            <div className="flex shrink-0 gap-1 overflow-x-auto border-b border-black/5 p-2">{([['chat','Codex'],['files','文件'],['workflow','工作流'],['git','Git']] as const).map(([key,label]) => <button type="button" key={key} onClick={() => setTab(key)} className={`rounded-full px-3 py-2 text-sm ${tab === key ? 'bg-black text-white' : 'text-black/55 hover:bg-black/5'}`}>{label}</button>)}<button type="button" onClick={() => void refresh()} className="ml-auto grid h-9 w-9 shrink-0 place-items-center rounded-full text-black/40 hover:bg-black/5" aria-label="刷新"><RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} /></button></div>
            <div className={`min-h-0 flex-1 p-4 sm:p-5 ${tab === 'chat' ? 'overflow-hidden' : 'overflow-y-auto'}`}>
              {tab === 'chat' ? <WorkspaceChat workspace={workspace} session={session} events={events} onEvents={setEvents} onSessionUpdate={(next) => { setSession(next); setSessions((items) => items.map((item) => item.id === next.id ? next : item)); }} onNew={() => setNewSessionOpen(true)} onDelete={() => session && setDeleteSession(session)} onRename={() => { if (session) { setSessionTitle(session.title); setRenameSessionOpen(true); } }} /> : null}
              {tab === 'files' ? <WorkspaceFiles workspace={workspace} files={files} onRefresh={() => void refresh()} onPreview={setFilePreview} /> : null}
              {tab === 'workflow' ? <WorkspaceWorkflow workspace={workspace} proposals={proposals} runs={workflowRuns} onRefresh={() => void refresh()} /> : null}
              {tab === 'git' ? <WorkspaceGit workspace={workspace} /> : null}
            </div>
          </section>
        </div>
      </main>
      <NewSessionDialog open={newSessionOpen} onClose={() => setNewSessionOpen(false)} onCreate={addSession} />
      <PromptDialog open={renameSessionOpen} onClose={() => setRenameSessionOpen(false)} onConfirm={renameSession} title="重命名会话" inputLabel="会话名称" value={sessionTitle} onChange={setSessionTitle} disabled={!sessionTitle.trim() || sessionTitle.trim() === session?.title} />
      <FilePreviewDialog workspace={workspace} file={filePreview} onClose={() => setFilePreview(null)} />
      <SessionSearchDialog open={sessionSearchOpen} workspaceId={workspace.id} onClose={() => setSessionSearchOpen(false)} onSelect={(next) => { setSessions((current) => current.some((item) => item.id === next.id) ? current : [next, ...current]); setSession(next); setSessionSearchOpen(false); }} />
      <ConfirmDialog open={deleteSession !== null} onClose={() => setDeleteSession(null)} onConfirm={async () => { if (deleteSession) { await api.deleteWorkspaceSession(deleteSession.id); setSessions((items) => items.filter((item) => item.id !== deleteSession.id)); if (session?.id === deleteSession.id) setSession(null); setDeleteSession(null); } }} title="删除会话？" description="会话记录和 Codex thread 将被永久删除。" confirmLabel="删除" tone="danger" />
    </div>
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
  return <AppDialog open={open} onClose={onClose} title="搜索会话" widthClassName="max-w-xl"><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题或对话内容" className="h-11 w-full rounded-xl border border-black/10 px-3 text-sm outline-none focus:border-black/25" /><div className="mt-3 max-h-[50vh] space-y-1 overflow-y-auto">{results.length ? results.map((item) => <button type="button" key={item.id} onClick={() => onSelect(item)} className="w-full rounded-xl px-3 py-3 text-left hover:bg-black/[0.04]"><div className="flex items-center gap-2 text-sm font-medium"><MessageSquare className="h-3.5 w-3.5 text-black/40" />{item.title}</div>{item.match_context ? <div className="mt-1 line-clamp-2 text-xs leading-5 text-black/45">{item.match_context}</div> : <div className="mt-1 text-xs text-black/35">{item.updated_at}</div>}</button>) : <div className="py-10 text-center text-xs text-black/35">{query ? '没有匹配的会话' : '输入关键词搜索'}</div>}</div></AppDialog>;
}

function WorkspaceChat({ workspace, session, events, onEvents, onSessionUpdate, onNew, onDelete, onRename }: { workspace: Workspace; session: WorkspaceSession | null; events: WorkspaceEvent[]; onEvents(next: WorkspaceEvent[]): void; onSessionUpdate(next: WorkspaceSession): void; onNew(): void; onDelete(): void; onRename(): void }) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<UploadRef[]>([]);
  const [uploading, setUploading] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [actionStatus, setActionStatus] = useState('');
  const [capabilities, setCapabilities] = useState<{ skills: string[]; mcp: string[] }>({ skills: [], mcp: [] });
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const waiting = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      const request = workspaceWaitingRequest(event);
      if (request) return { request, turnId: event.turn_id ?? turnId };
      const status = String(event.payload?.status ?? '');
      if (['success', 'failed', 'cancelled', 'completed'].includes(status)) return null;
    }
    return null;
  }, [events, turnId]);
  useEffect(() => {
    const container = messagesRef.current;
    if (container) container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
  }, [events.length]);
  useEffect(() => { void api.getSettings().then((settings) => { setModels(settings.supported_models); setCapabilities({ skills: settings.skills.filter((item) => item.enabled).map((item) => item.name), mcp: settings.mcp_servers.filter((item) => item.enabled).map((item) => item.name) }); }).catch(() => undefined); }, []);
  const lastEvent = events.at(-1);
  const terminalStatus = lastEvent?.event_type === 'turn_completed'
    ? String(lastEvent.payload?.status ?? '')
    : lastEvent?.event_type === 'error' ? 'failed' : '';
  useEffect(() => {
    if (!session || !['success', 'failed', 'cancelled', 'completed'].includes(terminalStatus)) return;
    if (session.status !== 'idle') onSessionUpdate({ ...session, status: 'idle' });
  }, [terminalStatus]);
  if (!session) return <div className="flex min-h-80 flex-col items-center justify-center text-center"><Bot className="h-8 w-8 text-black/20" /><div className="mt-3 text-sm font-medium text-black/65">选择或创建一个会话</div></div>;
  const send = async () => {
    if (!text.trim() || sending) return;
    const command = text.trim().split(/\s+/, 1)[0];
    if (command === '/new') { setText(''); onNew(); return; }
    if (command === '/status') { setActionStatus(`Runtime ${workspace.runtime_status} · Session ${session.status} · Skills ${capabilities.skills.length} · MCP ${capabilities.mcp.length}`); setText(''); return; }
    if (command === '/model') { const requested = text.trim().slice('/model'.length).trim(); if (requested && models.includes(requested)) setModel(requested); setActionStatus(requested ? `下一轮模型：${requested}` : `当前模型：${model || '默认'}`); setText(''); return; }
    if (command === '/compact' || command === '/review') { setSending(true); try { await api.runWorkspaceSessionAction(session.id, command === '/compact' ? 'compact' : 'review', text.trim().slice(command.length).trim() || undefined); setActionStatus(command === '/compact' ? '上下文压缩完成' : 'Review 已启动'); setText(''); } finally { setSending(false); } return; }
    if (command.startsWith('/') && command !== '/') { setActionStatus(`不支持 ${command}。可用：/new、/compact、/review、/model、/status；不提供 /diff。`); return; }
    setSending(true);
    try {
      const turn = await api.startWorkspaceTurn(session.id, { text: text.trim(), attachments: attachments.map((item) => ({ id: item.id, name: item.name })), model: model || undefined });
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
  const submitDecision = async (answers: DecisionAnswer[]) => {
    if (!waiting?.turnId) return;
    await api.resumeWorkspaceTurn(waiting.turnId, { request_id: waiting.request.request_id, answers });
    onSessionUpdate({ ...session, status: 'running' });
  };
  return <div className="flex h-full min-h-0 flex-col"><div className="mb-4 flex shrink-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="text-base font-semibold">{session.title}</h2><p className="mt-0.5 text-xs text-black/40">{session.thread_id ? `Codex thread 已连接 · ${model || '默认模型'}` : '尚未开始对话'}</p></div><div className="flex w-full items-center justify-end gap-1 sm:w-auto"><button type="button" onClick={() => setToolsOpen(true)} className="rounded-full px-3 py-1.5 text-xs text-black/45 hover:bg-black/5 hover:text-black">会话工具</button><button type="button" onClick={onRename} className="rounded-full px-3 py-1.5 text-xs text-black/45 hover:bg-black/5 hover:text-black">重命名</button><button type="button" onClick={onDelete} className="rounded-full px-3 py-1.5 text-xs text-red-500 hover:bg-red-50">删除</button></div></div>{actionStatus ? <div className="mb-3 shrink-0 rounded-xl border border-black/5 bg-black/[0.025] px-3 py-2 text-xs text-black/55">{actionStatus}</div> : null}<div ref={messagesRef} aria-live="polite" className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain rounded-2xl bg-[#F4F5F7] p-3">{events.length === 0 ? <div className="flex h-full items-center justify-center text-xs text-black/35">告诉 Codex 你想在这个项目中完成什么。</div> : collapseWorkspaceEvents(events).map((event) => <EventBubble key={event.id} event={event} />)}{waiting ? <DecisionPromptPanel context={waiting.request.context} groups={waiting.request.groups} disabled={sending} onComplete={(answers) => void submitDecision(answers)} /> : null}</div><div className="mt-3 shrink-0 rounded-2xl border border-black/10 bg-white p-2 shadow-pill">{attachments.length ? <div className="flex flex-wrap gap-1.5 px-2 pt-1">{attachments.map((item) => <button type="button" key={item.id} onClick={() => setAttachments((current) => current.filter((entry) => entry.id !== item.id))} className="inline-flex items-center gap-1 rounded-full bg-black/[0.05] px-2.5 py-1 text-[11px] text-black/60">{item.name}<X className="h-3 w-3" /></button>)}</div> : null}<textarea value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} placeholder="向 Codex 描述下一步…" className="min-h-16 w-full resize-none bg-transparent px-2 py-1.5 text-sm leading-6 outline-none placeholder:text-black/35" disabled={isRunning || waiting !== null} /><div className="flex items-center justify-end px-1"><div className="flex items-center gap-1"><label className={`grid h-8 w-8 place-items-center rounded-full text-black/35 hover:bg-black/5 ${isRunning ? 'pointer-events-none opacity-30' : 'cursor-pointer'}`} aria-label="添加附件"><Paperclip className="h-4 w-4" /><input type="file" multiple className="hidden" onChange={(event) => void uploadAttachments(event)} /></label>{isRunning && turnId ? <button type="button" onClick={() => void api.interruptWorkspaceTurn(turnId)} className="grid h-8 w-8 place-items-center rounded-full text-red-500 hover:bg-red-50" aria-label="停止"><Square className="h-3.5 w-3.5 fill-current" /></button> : <button type="button" onClick={() => void send()} disabled={!text.trim() || uploading || waiting !== null} className="grid h-8 w-8 place-items-center rounded-full bg-black text-white hover:bg-black/85 disabled:opacity-30" aria-label="发送"><Send className="h-4 w-4" /></button>}</div></div></div><SessionToolsDialog open={toolsOpen} onClose={() => setToolsOpen(false)} workspace={workspace} session={session} models={models} model={model} capabilities={capabilities} onModel={setModel} onStatus={setActionStatus} /></div>;
}

function workspaceWaitingRequest(event: WorkspaceEvent): RunWaitingRequest | null {
  if (!event.event_type.toLowerCase().includes('waiting') && !event.event_type.toLowerCase().includes('requestuserinput') && event.event_type !== 'decision_request') return null;
  const candidate = (event.payload.request ?? event.payload) as Partial<RunWaitingRequest>;
  if (!candidate.context || !Array.isArray(candidate.groups) || typeof candidate.request_id !== 'string') return null;
  return candidate as RunWaitingRequest;
}

function SessionToolsDialog({ open, onClose, workspace, session, models, model, capabilities, onModel, onStatus }: { open: boolean; onClose(): void; workspace: Workspace; session: WorkspaceSession; models: string[]; model: string; capabilities: { skills: string[]; mcp: string[] }; onModel(value: string): void; onStatus(value: string): void }) {
  const [goal, setGoal] = useState('');
  const [processes, setProcesses] = useState<Array<{ process_id: string; status: string; duration_ms?: number }>>([]);
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    const [goalResult, processResult] = await Promise.all([api.getWorkspaceGoal(session.id), api.getWorkspaceProcesses(session.id)]);
    setGoal(goalResult?.objective ?? '');
    setProcesses(processResult);
  };
  useEffect(() => { if (open && session.thread_id) void refresh().catch(() => undefined); }, [open, session.id, session.thread_id]);
  const action = async (kind: 'compact' | 'review' | 'archive') => {
    setBusy(true);
    try { await api.runWorkspaceSessionAction(session.id, kind); onStatus(kind === 'compact' ? '上下文压缩完成' : kind === 'review' ? 'Review 已启动' : 'Session 已归档'); } finally { setBusy(false); }
  };
  return <AppDialog open={open} onClose={onClose} title="会话工具" widthClassName="max-w-xl"><div className="space-y-4"><div className="grid grid-cols-2 gap-2 text-xs"><div className="rounded-2xl bg-[#F4F5F7] p-3"><div className="text-black/40">Runtime</div><div className="mt-1 font-medium">{workspace.runtime_status}</div></div><div className="rounded-2xl bg-[#F4F5F7] p-3"><div className="text-black/40">Session</div><div className="mt-1 font-medium">{session.status}</div></div></div><label className="block text-xs font-medium text-black/55">下一轮模型<select value={model} onChange={(event) => onModel(event.target.value)} className="mt-2 h-10 w-full rounded-xl border border-black/10 bg-white px-3 text-sm"><option value="">使用默认模型</option>{models.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><div className="flex flex-wrap gap-2"><button type="button" disabled={busy || !session.thread_id} onClick={() => void action('compact')} className="rounded-full border border-black/10 px-3 py-2 text-xs disabled:opacity-40">Compact</button><button type="button" disabled={busy || !session.thread_id} onClick={() => void action('review')} className="rounded-full border border-black/10 px-3 py-2 text-xs disabled:opacity-40">Review</button><button type="button" disabled={busy || !session.thread_id} onClick={() => void action('archive')} className="rounded-full border border-black/10 px-3 py-2 text-xs disabled:opacity-40">Archive</button></div><div className="rounded-2xl border border-black/5 p-3"><div className="text-xs font-semibold text-black/55">Goal</div><textarea value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="为这个长期任务设置明确目标" className="mt-2 min-h-20 w-full resize-none rounded-xl border border-black/10 px-3 py-2 text-sm outline-none focus:border-black/25" /><div className="mt-2 flex gap-2"><button type="button" disabled={!session.thread_id || !goal.trim()} onClick={() => void api.saveWorkspaceGoal(session.id, { objective: goal.trim(), status: 'active' })} className="rounded-full bg-black px-3 py-2 text-xs text-white disabled:opacity-40">保存 Goal</button><button type="button" disabled={!session.thread_id} onClick={() => void api.clearWorkspaceGoal(session.id).then(() => setGoal(''))} className="rounded-full border border-black/10 px-3 py-2 text-xs disabled:opacity-40">清除</button></div></div><div className="rounded-2xl border border-black/5 p-3"><div className="flex items-center justify-between"><div className="text-xs font-semibold text-black/55">后台进程</div><div className="flex gap-1"><button type="button" disabled={!session.thread_id} onClick={() => void refresh()} className="rounded-full px-2 py-1 text-[11px] text-black/45 hover:bg-black/5">刷新</button><button type="button" disabled={!session.thread_id || processes.length === 0} onClick={() => void api.cleanWorkspaceProcesses(session.id).then(refresh)} className="rounded-full px-2 py-1 text-[11px] text-black/45 hover:bg-black/5 disabled:opacity-30">清理</button></div></div>{processes.length ? <div className="mt-2 space-y-1.5">{processes.map((process) => <div key={process.process_id} className="flex items-center gap-2 rounded-xl bg-[#F4F5F7] px-3 py-2 text-xs"><span className="min-w-0 flex-1 truncate">进程 {process.process_id}</span><span className="text-black/40">{process.status}{process.duration_ms != null ? ` · ${(process.duration_ms / 1000).toFixed(1)}s` : ''}</span><button type="button" onClick={() => void api.stopWorkspaceProcess(session.id, process.process_id).then(refresh)} className="text-red-500">停止</button></div>)}</div> : <div className="mt-2 text-xs text-black/35">没有后台进程</div>}</div></div></AppDialog>;
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
    if (pending && (event.event_type === 'turn_completed' || event.event_type === 'error')) {
      visible.push(pending);
      pending = null;
    }
    visible.push(event);
  }
  if (pending) visible.push(pending);
  return visible;
}

function EventBubble({ event }: { event: WorkspaceEvent }) {
  const payload = event.payload ?? {};
  if (event.event_type.startsWith('workflow_run_')) {
    const name = String(payload.app_name ?? '工作流');
    const status = event.event_type === 'workflow_run_started' ? '运行中' : event.event_type === 'workflow_run_waiting' ? '等待回复' : payload.status === 'success' ? '已完成' : '运行失败';
    return <div className="flex justify-start"><div className="inline-flex max-w-[88%] items-center gap-2 rounded-xl border border-black/5 bg-white px-3 py-2 text-xs text-black/55 shadow-sm"><GitBranch className="h-3.5 w-3.5" /><span className="truncate">{name}</span><span className="text-black/35">{status}</span></div></div>;
  }
  const text = String(payload.text ?? payload.content ?? payload.delta ?? payload.message ?? '');
  const role = String(payload.role ?? payload.author ?? 'assistant');
  const messageAttachments = workspaceMessageAttachments(payload.attachments);
  if (!text && messageAttachments.length === 0 && event.event_type !== 'status') return null;
  const dark = role === 'user';
  return <div className={`flex ${dark ? 'justify-end' : 'justify-start'}`}><div className={`max-w-[88%] break-words rounded-2xl px-3.5 py-2.5 text-sm leading-6 ${dark ? 'bg-black text-white' : 'bg-white text-black/75 shadow-sm'}`}>{text ? <div>{text}</div> : null}{messageAttachments.length ? <div className={`${text ? 'mt-2' : ''} flex flex-wrap justify-end gap-2`}>{messageAttachments.map((attachment, index) => <WorkspaceMessageAttachment key={attachment.id || `${attachment.name}-${index}`} attachment={attachment} dark={dark} />)}</div> : null}</div></div>;
}

interface WorkspaceMessageAttachmentValue {
  id?: string;
  name: string;
  mime?: string;
}

function workspaceMessageAttachments(value: unknown): WorkspaceMessageAttachmentValue[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === 'string' && item.trim()) return [{ name: item.trim() }];
    if (!item || typeof item !== 'object') return [];
    const record = item as Record<string, unknown>;
    const name = typeof record.name === 'string' ? record.name.trim() : '';
    if (!name) return [];
    return [{ id: typeof record.id === 'string' ? record.id : undefined, name, mime: typeof record.mime === 'string' ? record.mime : undefined }];
  });
}

function WorkspaceMessageAttachment({ attachment, dark }: { attachment: WorkspaceMessageAttachmentValue; dark: boolean }) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const isImage = Boolean(attachment.id && attachment.mime?.startsWith('image/'));
  useEffect(() => {
    if (!isImage || !attachment.id) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    void api.fetchUploadBlob(attachment.id, controller.signal).then((blob) => {
      objectUrl = URL.createObjectURL(blob);
      setImageUrl(objectUrl);
    }).catch(() => setImageUrl(null));
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [attachment.id, isImage]);
  if (imageUrl) return <figure className={`w-40 overflow-hidden rounded-xl border ${dark ? 'border-white/15 bg-white/5' : 'border-black/10 bg-black/[0.03]'}`}><img src={imageUrl} alt={attachment.name} className="max-h-52 w-full object-contain" /><figcaption className={`truncate border-t px-2 py-1 text-[11px] leading-4 ${dark ? 'border-white/10 text-white/70' : 'border-black/10 text-black/55'}`}>{attachment.name}</figcaption></figure>;
  return <div className={`inline-flex max-w-56 items-center gap-1.5 rounded-xl px-2.5 py-1.5 text-xs leading-5 ${dark ? 'bg-white/10' : 'bg-black/[0.05]'}`}><File className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{attachment.name}</span></div>;
}

function WorkspaceFiles({ workspace, files, onRefresh, onPreview }: { workspace: Workspace; files: WorkspaceFile[]; onRefresh(): void; onPreview(file: WorkspaceFile): void }) { const [uploading, setUploading] = useState(false); const upload = async (event: React.ChangeEvent<HTMLInputElement>) => { const selected = Array.from(event.target.files ?? []); if (!selected.length) return; setUploading(true); try { await api.uploadWorkspaceFiles(workspace.id, selected); onRefresh(); } finally { setUploading(false); event.target.value = ''; } }; return <div><div className="mb-4 flex items-center justify-between"><h2 className="text-base font-semibold">项目文件</h2><label className="inline-flex cursor-pointer items-center gap-1.5 rounded-full bg-black px-3 py-2 text-xs font-medium text-white hover:bg-black/85"><Upload className="h-3.5 w-3.5" />{uploading ? '上传中…' : '上传'}<input type="file" multiple className="hidden" onChange={(event) => void upload(event)} /></label></div><div className="divide-y divide-black/5 rounded-2xl border border-black/5">{files.length ? files.map((file) => <button type="button" key={file.path} onClick={() => file.kind === 'file' && onPreview(file)} className="flex w-full items-center gap-3 px-3.5 py-3 text-left hover:bg-black/[0.02]"><span className="grid h-8 w-8 place-items-center rounded-xl bg-black/[0.04]">{file.kind === 'directory' ? <Folder className="h-4 w-4 text-black/45" /> : <File className="h-4 w-4 text-black/45" />}</span><span className="min-w-0 flex-1 truncate text-sm text-black/75">{file.path}</span><span className="text-[11px] text-black/35">{file.kind === 'file' ? formatSize(file.size) : ''}</span><ChevronRight className="h-4 w-4 text-black/20" /></button>) : <div className="px-4 py-12 text-center text-xs text-black/40">目录为空</div>}</div></div>; }

function FilePreviewDialog({ workspace, file, onClose }: { workspace: Workspace; file: WorkspaceFile | null; onClose(): void }) { const [preview, setPreview] = useState<Awaited<ReturnType<typeof api.previewWorkspaceFile>> | null>(null); useEffect(() => { if (!file) { setPreview(null); return; } void api.previewWorkspaceFile(workspace.id, file.path).then(setPreview).catch(() => setPreview(null)); }, [file, workspace.id]); const download = async () => { if (!file) return; const blob = await api.downloadWorkspaceFile(workspace.id, file.path); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.name; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0); }; return <AppDialog open={file !== null} onClose={onClose} title={file?.name ?? '文件预览'} description={file?.path} widthClassName="max-w-3xl" footer={preview ? <button type="button" onClick={() => void download()} className="inline-flex items-center gap-1.5 rounded-full bg-black px-4 py-2 text-sm font-medium text-white"><Download className="h-4 w-4" />下载</button> : undefined}>{preview ? <div className="max-h-[60vh] overflow-auto rounded-2xl bg-[#F4F5F7] p-4">{preview.content != null ? <pre className="whitespace-pre-wrap break-words text-xs leading-6 text-black/75">{preview.content}</pre> : <div className="text-sm text-black/45">此文件类型仅支持下载。</div>}</div> : <div className="py-12 text-center text-sm text-black/40">加载预览…</div>}</AppDialog>; }

function WorkspaceWorkflow({ workspace, proposals, runs, onRefresh }: { workspace: Workspace; proposals: WorkspaceWorkflowProposal[]; runs: WorkspaceWorkflowRun[]; onRefresh(): void }) {
  const [selected, setSelected] = useState<WorkspaceWorkflowProposal | null>(null);
  const [busy, setBusy] = useState(false);
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
    <div>
      <h2 className="mb-4 text-base font-semibold">可视化工作流</h2>
      {runs.length ? <div className="mb-5 space-y-2"><div className="text-xs font-semibold text-black/55">对话调用记录</div>{runs.map((run) => <div key={run.run_id} className="flex items-center gap-3 rounded-xl border border-black/5 px-3 py-2.5"><span className={`h-2 w-2 rounded-full ${run.status === 'success' ? 'bg-emerald-500' : run.status === 'failed' ? 'bg-red-500' : 'bg-amber-500'}`} /><span className="min-w-0 flex-1 truncate text-sm text-black/70">{run.app_name}</span><span className="text-xs text-black/40">{run.status}</span></div>)}</div> : null}
      {proposals.length ? <div className="mb-3 space-y-3">{proposals.map((proposal) => <button type="button" key={proposal.id} onClick={() => setSelected(proposal)} className="flex w-full items-center gap-3 rounded-2xl border border-black/5 p-4 text-left hover:border-black/15"><span className={`grid h-9 w-9 place-items-center rounded-xl ${proposal.status === 'pending' ? 'bg-amber-50 text-amber-600' : 'bg-emerald-50 text-emerald-600'}`}><GitBranch className="h-4 w-4" /></span><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium">{proposal.name}</span><span className="mt-0.5 block text-xs text-black/40">{proposal.kind === 'create' ? '新建应用' : '修改应用'} · {proposal.status}</span></span><ChevronRight className="h-4 w-4 text-black/25" /></button>)}</div> : null}
      <AppDialog open={selected !== null} onClose={() => !busy && setSelected(null)} title={selected?.name ?? '工作流提案'} description={selected?.description} widthClassName="max-w-4xl" footer={selected?.status === 'pending' ? <><button type="button" onClick={() => { if (selected) void api.rejectWorkspaceWorkflowProposal(workspace.id, selected.id).then(() => { setSelected(null); onRefresh(); }); }} className="rounded-full border border-black/10 px-4 py-2 text-sm">拒绝</button><button type="button" disabled={busy} onClick={() => void confirm()} className="inline-flex items-center gap-1.5 rounded-full bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-40"><Check className="h-4 w-4" />确认应用</button></> : undefined}>
        {selected ? <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_220px]"><div className="rounded-2xl bg-[#F4F5F7] p-4"><div className="text-xs font-semibold text-black/55">Graph 预览</div><div className="mt-3 h-[360px] overflow-hidden rounded-xl border border-black/10 bg-white"><ReactFlow nodes={graphNodes} edges={graphEdges} fitView nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} zoomOnScroll={false} proOptions={{ hideAttribution: true }}><Background color="#e5e7eb" gap={20} /><Controls showInteractive={false} /></ReactFlow></div></div><div className="rounded-2xl border border-black/5 bg-white p-4"><div className="text-xs font-semibold text-black/55">Lint</div><div className={`mt-2 text-sm ${selected.lint.ok ? 'text-emerald-600' : 'text-red-600'}`}>{selected.lint.ok ? '通过' : '需要修正'}</div>{selected.lint.issues.map((issue, index) => <div key={index} className="mt-2 text-xs leading-5 text-black/50">{issue.detail}</div>)}</div></div> : null}
      </AppDialog>
    </div>
  );
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
  return <div><div className="mb-4"><h2 className="text-base font-semibold">Git 配置</h2><p className="mt-0.5 text-xs text-black/40">仅支持管理员白名单内的私有 HTTPS 仓库；令牌不会显示或进入 Codex 容器。</p></div><div className="space-y-3 rounded-2xl border border-black/5 bg-[#F4F5F7] p-4"><input value={repositoryUrl} onChange={(event) => setRepositoryUrl(event.target.value)} placeholder="https://git.example.com/team/project.git" className="h-10 w-full rounded-xl border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/25" /><div className="grid gap-2 sm:grid-cols-2"><input value={branch} onChange={(event) => setBranch(event.target.value)} placeholder="默认分支（main）" className="h-10 rounded-xl border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/25" /><input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" placeholder={config.token_configured ? '已配置令牌；留空不修改' : '访问令牌（可选）'} className="h-10 rounded-xl border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/25" /></div><div className="flex items-center justify-between gap-3"><span className="text-[11px] text-black/40">允许的主机：{config.allowed_hosts.join('、') || '管理员尚未配置'}</span><button type="button" disabled={busy || !repositoryUrl.trim()} onClick={() => void save()} className="rounded-full bg-black px-3 py-2 text-xs font-medium text-white disabled:opacity-40">保存配置</button></div></div></div>;
}

function NewSessionDialog({ open, onClose, onCreate }: { open: boolean; onClose(): void; onCreate(title: string): Promise<void> }) { const [title, setTitle] = useState(''); return <AppDialog open={open} onClose={onClose} title="新建会话" footer={<><button type="button" onClick={onClose} className="rounded-full border border-black/10 px-4 py-2 text-sm">取消</button><button type="button" disabled={!title.trim()} onClick={() => void onCreate(title.trim())} className="rounded-full bg-black px-4 py-2 text-sm text-white disabled:opacity-40">创建</button></>}><input value={title} onChange={(event) => setTitle(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && title.trim()) void onCreate(title.trim()); }} autoFocus placeholder="例如：重构登录页" className="h-11 w-full rounded-xl border border-black/10 px-3 text-sm outline-none focus:border-black/30" /></AppDialog>; }

function PlusIcon() { return <span className="text-lg leading-none">+</span>; }
function formatSize(size: number) { if (size < 1024) return `${size} B`; if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`; return `${(size / 1024 / 1024).toFixed(1)} MB`; }
