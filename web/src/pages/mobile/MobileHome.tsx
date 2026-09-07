import { useEffect, useMemo, useState } from 'react';
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react';
import { useNavigate } from 'react-router-dom';
import { BookOpen, ChevronDown, Copy, Layers3, LogOut, MoreHorizontal, Pencil, Play, Plus, Search, Settings, Sparkles, Trash2 } from 'lucide-react';
import { useAppStore } from '../../stores/useAppStore';
import { useAuthStore } from '../../stores/useAuthStore';
import { showCaughtError, showErrorDialog } from '../../stores/useErrorDialogStore';
import type { App } from '../../types';
import { useAppCoverUrl } from '../../hooks/useAppCoverUrl';
import { AiActivityDock } from '../../components/common/AiActivityDock';
import { ConfirmDialog } from '../../components/common/ConfirmDialog';
import { PromptDialog } from '../../components/common/PromptDialog';
import { WorkspaceLibrary } from '../../components/workspace/WorkspaceLibrary';

type Tab = 'mine' | 'recent' | 'market' | 'workspaces';
type MobileListItem = {
  app: App;
  kind: 'mine' | 'recent' | 'template' | 'market';
};

export function MobileHome() {
  const navigate = useNavigate();
  const { myApps, templates, market, recentRuns, loading, error, load, createBlank, cloneFromMarket, cloneTemplate, rename, remove } = useAppStore();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const [tab, setTab] = useState<Tab>('mine');
  const [query, setQuery] = useState('');
  const [cloningId, setCloningId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [renameTarget, setRenameTarget] = useState<App | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<App | null>(null);
  const [nextName, setNextName] = useState('');
  const [dialogBusy, setDialogBusy] = useState(false);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (error) showErrorDialog(error, '加载失败'); }, [error]);

  const source = useMemo<MobileListItem[]>(() => {
    if (tab === 'mine') return myApps.map((app) => ({ app, kind: 'mine' }));
    if (tab === 'recent') return recentRuns.map((app) => ({ app, kind: 'recent' }));
    if (tab === 'market') return [
      ...templates.map((app) => ({ app, kind: 'template' as const })),
      ...market.map((app) => ({ app, kind: 'market' as const })),
    ];
    return [];
  }, [myApps, recentRuns, market, templates, tab]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? source.filter(({ app }) => `${app.name} ${app.description}`.toLowerCase().includes(q)) : source;
  }, [source, query]);

  const createApp = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const app = await createBlank();
      navigate(`/m/apps/${app.id}/editor`);
    } catch (error) {
      showCaughtError(error, '创建失败', '创建失败');
    } finally { setCreating(false); }
  };

  const cloneApp = async (app: App, template = false) => {
    if (cloningId) return;
    setCloningId(app.id);
    try {
      const cloned = await (template ? cloneTemplate(app.id) : cloneFromMarket(app.id));
      navigate(`/m/apps/${cloned.id}/editor`);
    } catch (error) {
      showCaughtError(error, template ? '导入模板失败' : '复制失败', '创建失败');
    } finally { setCloningId(null); }
  };

  return (
    <div className="min-h-dvh bg-[#F4F5F7] text-[#0B0B0F]">
      <header className="sticky top-0 z-20 border-b border-black/10 bg-white px-4 pb-3 pt-[calc(env(safe-area-inset-top)+8px)]">
        <div className="flex h-11 items-center gap-2">
          <div className="mr-auto text-xl font-semibold tracking-tight">Mira</div>
          <AiActivityDock compact />
          <Menu>
            <MenuButton className="flex h-11 min-w-0 max-w-40 items-center gap-1.5 rounded-lg px-2 text-sm text-black/60" aria-label="账户菜单">
              <span className="truncate">{user?.username ?? '账户'}</span><ChevronDown className="h-4 w-4 shrink-0" />
            </MenuButton>
            <MenuItems anchor="bottom end" className="z-[85] mt-1 w-44 rounded-xl border border-black/10 bg-white p-1 text-sm shadow-lg focus:outline-none">
              <MenuItem><button onClick={() => navigate('/m/wiki')} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5"><BookOpen className="h-4 w-4" />我的 Wiki</button></MenuItem>
              {user?.is_admin ? <MenuItem><button onClick={() => navigate('/m/settings')} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5"><Settings className="h-4 w-4" />系统设置</button></MenuItem> : null}
              <MenuItem><button onClick={() => { logout(); navigate('/m/login'); }} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5"><LogOut className="h-4 w-4" />登出</button></MenuItem>
            </MenuItems>
          </Menu>
        </div>
        <div role="tablist" aria-label="首页功能" className="mt-1 grid grid-cols-4 rounded-lg bg-black/[0.04] p-1 text-xs">
          <TabButton active={tab === 'mine'} onClick={() => setTab('mine')}>我的应用</TabButton>
          <TabButton active={tab === 'recent'} onClick={() => setTab('recent')}>最近运行</TabButton>
          <TabButton active={tab === 'market'} onClick={() => setTab('market')}>应用市场</TabButton>
          <TabButton active={tab === 'workspaces'} onClick={() => setTab('workspaces')}>工作空间</TabButton>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 pb-[calc(env(safe-area-inset-bottom)+28px)] pt-4">
        {tab === 'workspaces' ? <WorkspaceLibrary mobile /> : <>
          <div className="mb-4 flex items-center gap-2">
            <div className="flex h-11 min-w-0 flex-1 items-center gap-2 rounded-lg border border-black/10 bg-white px-3">
              <Search className="h-4 w-4 shrink-0 text-black/35" />
              <input aria-label="搜索应用" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索应用" className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-black/35" />
            </div>
            <button type="button" onClick={() => void createApp()} disabled={creating} className="flex h-11 shrink-0 items-center gap-1 rounded-lg bg-black px-3 text-sm font-medium text-white disabled:opacity-50"><Plus className="h-4 w-4" />{creating ? '创建中' : '新建'}</button>
          </div>
          {loading && source.length === 0 ? <div className="space-y-3">{[0, 1, 2].map((item) => <div key={item} className="h-40 animate-pulse rounded-xl bg-neutral-200/70" />)}</div> : null}
          {!loading && filtered.length === 0 ? (
            <div className="rounded-xl border border-dashed border-black/15 bg-white px-5 py-12 text-center">
              <Sparkles className="mx-auto h-7 w-7 text-black/25" />
              <div className="mt-3 text-sm font-medium text-black/70">{query ? '没有匹配的应用' : tab === 'mine' ? '创建你的第一个应用' : tab === 'recent' ? '还没有运行记录' : '暂无模板或市场应用'}</div>
              {!query && tab === 'mine' ? <button onClick={() => void createApp()} disabled={creating} className="mt-4 min-h-11 rounded-lg bg-black px-4 text-sm text-white disabled:opacity-50">新建应用</button> : null}
            </div>
          ) : null}
          <div className="space-y-3">
            {filtered.map(({ app, kind }) => (
              <MobileAppCard key={`${kind}-${app.id}`} app={app} kind={kind} busy={cloningId === app.id}
                onOpen={() => kind === 'template' ? void cloneApp(app, true) : navigate(`/m/apps/${app.id}/${kind === 'mine' && app.can_edit ? 'editor' : 'run'}`)}
                onRun={() => navigate(`/m/apps/${app.id}/run`)} onEdit={() => navigate(`/m/apps/${app.id}/editor`)}
                onClone={() => void cloneApp(app)}
                onRename={() => { setRenameTarget(app); setNextName(app.name); }} onDelete={() => setDeleteTarget(app)} />
            ))}
          </div>
        </>}
      </main>
      <PromptDialog open={renameTarget !== null} onClose={() => !dialogBusy && setRenameTarget(null)} title="重命名应用" inputLabel="应用名称" value={nextName} onChange={setNextName} busy={dialogBusy} disabled={!nextName.trim() || nextName.trim() === renameTarget?.name}
        onConfirm={async () => {
          if (!renameTarget || !nextName.trim()) return;
          setDialogBusy(true);
          try { await rename(renameTarget.id, nextName.trim()); setRenameTarget(null); await load(); }
          catch (error) { showCaughtError(error, '重命名失败', '重命名失败'); }
          finally { setDialogBusy(false); }
        }} />
      <ConfirmDialog open={deleteTarget !== null} onClose={() => !dialogBusy && setDeleteTarget(null)} title="删除应用？" description={deleteTarget ? `确定删除“${deleteTarget.name}”？此操作无法撤销。` : undefined} confirmLabel="删除" tone="danger" busy={dialogBusy}
        onConfirm={async () => {
          if (!deleteTarget) return;
          setDialogBusy(true);
          try { await remove(deleteTarget.id); setDeleteTarget(null); await load(); }
          catch (error) { showCaughtError(error, '删除失败', '删除失败'); }
          finally { setDialogBusy(false); }
        }} />
    </div>
  );
}

function MobileAppCard({ app, kind, busy, onOpen, onRun, onEdit, onClone, onRename, onDelete }: {
  app: App; kind: MobileListItem['kind']; busy: boolean;
  onOpen(): void; onRun(): void; onEdit(): void; onClone(): void; onRename(): void; onDelete(): void;
}) {
  const coverUrl = useAppCoverUrl(app);
  const editable = kind !== 'template' && app.can_edit && !app.archived_at;
  const canClone = kind !== 'template' && kind !== 'recent' && app.can_clone && !app.archived_at;
  const badge = app.archived_at ? '已下架' : kind === 'template' ? '模板' : app.status === 'published' ? app.visibility === 'private' ? '私有发布' : '已发布' : '草稿';
  return (
    <article className={`rounded-xl border border-black/10 bg-white ${busy ? 'opacity-60' : ''}`}>
      <div className="flex items-start gap-3 p-4 pb-3">
        <button type="button" onClick={onOpen} disabled={busy} aria-label={`打开 ${app.name}`} className="flex min-w-0 flex-1 items-start gap-3 text-left disabled:cursor-wait">
          <div className={`grid shrink-0 place-items-center overflow-hidden rounded-lg bg-black/[0.04] text-black/50 ${coverUrl ? 'aspect-video w-20' : 'h-12 w-12'}`}>
            {coverUrl ? <img src={coverUrl} alt="" className="h-full w-full object-cover" /> : <Layers3 className="h-5 w-5" />}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="line-clamp-2 break-words text-base font-semibold leading-6">{app.name}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-black/45"><span>{badge}</span>{app.can_view_source ? <span>{app.graph.nodes.length} 个步骤</span> : null}</div>
          </div>
        </button>
        {editable || canClone ? <Menu>
          <MenuButton disabled={busy} aria-label={`${app.name}菜单`} className="-mr-2 -mt-2 grid h-11 w-11 shrink-0 place-items-center rounded-lg text-black/50 hover:bg-black/5"><MoreHorizontal className="h-5 w-5" /></MenuButton>
          <MenuItems anchor="bottom end" className="z-[85] mt-1 w-44 rounded-xl border border-black/10 bg-white p-1 text-sm shadow-lg focus:outline-none">
            {editable ? <MenuItem><button onClick={onEdit} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5"><Pencil className="h-4 w-4" />编辑工作流</button></MenuItem> : null}
            {editable ? <MenuItem><button onClick={onRename} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5"><Pencil className="h-4 w-4" />重命名</button></MenuItem> : null}
            {canClone ? <MenuItem><button onClick={onClone} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left data-[focus]:bg-black/5"><Copy className="h-4 w-4" />{editable ? '复制应用' : '克隆应用'}</button></MenuItem> : null}
            {editable ? <MenuItem><button onClick={onDelete} className="flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left text-red-600 data-[focus]:bg-red-50"><Trash2 className="h-4 w-4" />删除</button></MenuItem> : null}
          </MenuItems>
        </Menu> : null}
      </div>
      {app.description ? <p className="mx-4 mb-3 line-clamp-2 break-words text-sm leading-6 text-black/55">{app.description}</p> : null}
      <div className="flex items-center justify-end gap-2 border-t border-black/5 px-3 py-2">
        {busy ? <span className="mr-auto text-xs text-black/50">正在创建副本…</span> : null}
        {editable ? <button onClick={onEdit} disabled={busy} className="min-h-11 rounded-lg px-3 text-sm text-black/60">编辑</button> : null}
        <button onClick={kind === 'template' ? onOpen : onRun} disabled={busy} className="flex min-h-11 items-center gap-1.5 rounded-lg bg-black/[0.05] px-3 text-sm font-medium disabled:opacity-50">{kind === 'template' ? <Copy className="h-4 w-4" /> : <Play className="h-4 w-4" />}{kind === 'template' ? '使用模板' : '运行'}</button>
      </div>
    </article>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick(): void; children: React.ReactNode }) {
  return <button type="button" role="tab" aria-selected={active} onClick={onClick} className={`h-11 rounded-md transition ${active ? 'bg-white font-medium text-black shadow-sm' : 'text-black/50 hover:text-black'}`}>{children}</button>;
}
