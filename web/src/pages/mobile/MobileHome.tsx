import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogOut, MoreVertical, Search, Sparkles } from 'lucide-react';
import { useAppStore } from '../../stores/useAppStore';
import { useAuthStore } from '../../stores/useAuthStore';
import { showCaughtError, showErrorDialog } from '../../stores/useErrorDialogStore';
import type { App } from '../../types';
import { useAppCoverUrl } from '../../hooks/useAppCoverUrl';

type Tab = 'mine' | 'recent' | 'market';
type MobileListItem = {
  app: App;
  kind: 'mine' | 'recent' | 'template' | 'market';
};

export function MobileHome() {
  const navigate = useNavigate();
  const { myApps, templates, market, recentRuns, loading, error, load, cloneFromMarket, cloneTemplate } = useAppStore();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const [tab, setTab] = useState<Tab>('mine');
  const [query, setQuery] = useState('');
  const [cloningId, setCloningId] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (error) showErrorDialog(error, '加载失败');
  }, [error]);

  const catalogItems = useMemo<MobileListItem[]>(
    () => [
      ...templates.map((app) => ({ app, kind: 'template' as const })),
      ...market.map((app) => ({ app, kind: 'market' as const })),
    ],
    [market, templates],
  );
  const source = useMemo<MobileListItem[]>(() => {
    if (tab === 'mine') return myApps.map((app) => ({ app, kind: 'mine' as const }));
    if (tab === 'recent') return recentRuns.map((app) => ({ app, kind: 'recent' as const }));
    return catalogItems;
  }, [catalogItems, myApps, recentRuns, tab]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return source;
    return source.filter(({ app }) => `${app.name} ${app.description}`.toLowerCase().includes(q));
  }, [source, query]);

  const openApp = async (app: App) => {
    navigate(`/m/apps/${app.id}/run`);
  };

  const cloneMarketApp = async (app: App) => {
    setCloningId(app.id);
    try {
      const cloned = await cloneFromMarket(app.id);
      navigate(`/m/apps/${cloned.id}/run`);
    } catch (error) {
      showCaughtError(error, '克隆失败', '克隆失败');
    } finally {
      setCloningId(null);
    }
  };

  const useTemplate = async (app: App) => {
    setCloningId(app.id);
    try {
      const cloned = await cloneTemplate(app.id);
      navigate(`/m/apps/${cloned.id}/run`);
    } catch (error) {
      showCaughtError(error, '导入模板失败', '导入失败');
    } finally {
      setCloningId(null);
    }
  };

  const handleLogout = () => {
    logout();
    navigate('/m/login');
  };

  return (
    <div className="min-h-dvh bg-[#F4F5F7] text-[#0B0B0F]">
      <header className="sticky top-0 z-20 border-b border-black/5 bg-white/85 px-4 pb-3 pt-[calc(env(safe-area-inset-top)+10px)] backdrop-blur">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xl font-semibold tracking-tight">Mira</div>
            <div className="mt-0.5 max-w-[220px] truncate text-xs text-black/45">
              {user?.username ?? 'Mobile Run'}
            </div>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            className="grid h-10 w-10 place-items-center rounded-full text-black/55 hover:bg-black/5 hover:text-black"
            aria-label="登出"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-4 flex items-center gap-2 rounded-full border border-black/10 bg-white px-3 py-2 shadow-pill">
          <Search className="h-4 w-4 shrink-0 text-black/35" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索应用"
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-black/35"
          />
        </div>
        <div className="mt-3 grid grid-cols-3 rounded-full bg-black/[0.05] p-1 text-sm">
          <TabButton active={tab === 'mine'} onClick={() => setTab('mine')}>我的应用</TabButton>
          <TabButton active={tab === 'recent'} onClick={() => setTab('recent')}>最近运行</TabButton>
          <TabButton active={tab === 'market'} onClick={() => setTab('market')}>应用市场</TabButton>
        </div>
      </header>

      <main className="px-4 pb-[calc(env(safe-area-inset-bottom)+28px)] pt-4">
        {loading && source.length === 0 ? (
          <div className="space-y-3">
            {[0, 1, 2].map((item) => (
              <div key={item} className="aspect-video animate-pulse rounded-[22px] bg-neutral-200/70" />
            ))}
          </div>
        ) : null}

        {!loading && filtered.length === 0 ? (
          <div className="rounded-[24px] border border-dashed border-black/15 bg-white/55 px-5 py-12 text-center">
            <Sparkles className="mx-auto h-7 w-7 text-black/25" />
            <div className="mt-3 text-sm font-medium text-black/70">
              {query ? '没有匹配的应用' : tab === 'mine' ? '还没有应用' : '暂无应用'}
            </div>
            <div className="mt-1 text-xs leading-5 text-black/45">
              {tab === 'mine'
                ? '在桌面端创建应用，或从应用市场使用模板、克隆应用后，可在这里运行。'
                : tab === 'recent'
                ? '运行过的应用会显示在这里。'
                : '模板和发布后的应用会显示在这里。'}
            </div>
          </div>
        ) : null}

        <div className="space-y-3">
          {filtered.map(({ app, kind }) => (
            <MobileAppCard
              key={`${kind}-${app.id}`}
              app={app}
              market={kind === 'template' || kind === 'market' || (kind === 'recent' && !app.can_edit)}
              hideMenu={kind !== 'market'}
              badgeLabel={kind === 'template' ? '模板' : undefined}
              cloning={cloningId === app.id}
              onOpen={() => void (kind === 'template' ? useTemplate(app) : openApp(app))}
              onClone={() => void cloneMarketApp(app)}
            />
          ))}
        </div>
      </main>
    </div>
  );
}

function MobileAppCard({
  app,
  market,
  cloning,
  hideMenu,
  badgeLabel,
  onOpen,
  onClone,
}: {
  app: App;
  market: boolean;
  cloning: boolean;
  hideMenu?: boolean;
  badgeLabel?: string;
  onOpen(): void;
  onClone(): void;
}) {
  const coverUrl = useAppCoverUrl(app);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [menuOpen]);

  return (
    <div
      className={`group relative aspect-video w-full overflow-hidden rounded-[22px] bg-neutral-950 text-left text-white shadow-card transition active:scale-[0.99] ${cloning ? 'opacity-60' : ''}`}
      style={
        coverUrl
          ? {
              backgroundImage: `linear-gradient(90deg, rgba(0,0,0,0.82), rgba(0,0,0,0.25)), url(${coverUrl})`,
              backgroundSize: 'cover',
              backgroundPosition: 'center',
            }
          : undefined
      }
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={cloning}
        className="flex h-full w-full min-w-0 flex-col justify-end p-4 text-left disabled:cursor-not-allowed"
      >
        <div className="mb-2 flex items-center gap-2">
          {app.archived_at ? (
            <span className="rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-medium text-white/80 backdrop-blur">
              已下架
            </span>
          ) : badgeLabel ? (
            <span className="rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-medium text-white/80 backdrop-blur">
              {badgeLabel}
            </span>
          ) : !market ? (
            <span className="rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-medium text-white/80 backdrop-blur">
              {app.status === 'published' ? app.visibility === 'private' ? '私有发布' : '已发布' : '草稿'}
            </span>
          ) : null}
          {app.can_view_source ? (
            <span className="text-[10px] text-white/55">
              {app.graph.nodes.length} nodes
            </span>
          ) : null}
        </div>
        <div className="line-clamp-2 text-lg font-semibold leading-tight">{app.name}</div>
        {app.description ? (
          <div className="mt-1 line-clamp-2 text-xs leading-5 text-white/68">{app.description}</div>
        ) : null}
        {cloning ? (
          <div className="mt-3 text-xs text-white/70">正在导入...</div>
        ) : null}
      </button>
      {market && app.can_clone && !hideMenu ? (
        <div ref={menuRef} className="absolute right-2 top-2">
          <button
            type="button"
            onClick={() => setMenuOpen((value) => !value)}
            className="grid h-9 w-9 place-items-center rounded-full bg-black/25 text-white/85 backdrop-blur hover:bg-black/35"
            aria-label="应用菜单"
          >
            <MoreVertical className="h-4 w-4" />
          </button>
          {menuOpen ? (
            <div className="absolute right-0 mt-1 w-28 rounded-2xl bg-white p-1 text-sm text-black shadow-card">
              <button
                type="button"
                disabled={cloning}
                onClick={() => {
                  setMenuOpen(false);
                  onClone();
                }}
                className="w-full rounded-xl px-3 py-2 text-left hover:bg-black/5 disabled:opacity-50"
              >
                克隆
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick(): void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-9 rounded-full transition ${
        active ? 'bg-white font-medium text-black shadow-sm' : 'text-black/50 hover:text-black'
      }`}
    >
      {children}
    </button>
  );
}
