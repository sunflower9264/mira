import { useState, useRef, useEffect, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import type { App } from '../../types';
import { DotsIcon } from '../common/Icons';
import { useAppCoverUrl } from '../../hooks/useAppCoverUrl';

interface Props {
  app: App;
  market?: boolean;
  busy?: boolean;
  busyLabel?: string;
  badgeLabel?: string;
  hideMenu?: boolean;
  onOpen?(app: App): void;
  onRequestRename?(app: App): void;
  onRequestDelete?(app: App): void;
  onClone?(id: string): void;
}

export function AppCard({
  app,
  market,
  busy,
  busyLabel,
  badgeLabel,
  hideMenu,
  onOpen,
  onRequestRename,
  onRequestDelete,
  onClone,
}: Props) {
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const coverUrl = useAppCoverUrl(app);
  const showMenu = !hideMenu && !app.archived_at && (!market || app.can_clone);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [menuOpen]);

  const goEditor = () => {
    if (busy) return;
    if (onOpen) {
      onOpen(app);
      return;
    }
    if (market) {
      navigate(`/market/apps/${app.id}`);
      return;
    }
    navigate(`/apps/${app.id}/editor`);
  };

  const handleCardKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.currentTarget !== e.target || (e.key !== 'Enter' && e.key !== ' ')) return;
    e.preventDefault();
    goEditor();
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={goEditor}
      onKeyDown={handleCardKeyDown}
      className={`group relative w-full aspect-video rounded-card overflow-hidden text-left bg-neutral-900 text-white shadow-card hover:shadow-lg transition-shadow cursor-pointer ${busy ? 'opacity-70' : ''}`}
      style={
        coverUrl
          ? { backgroundImage: `linear-gradient(180deg, rgba(0,0,0,0.05), rgba(0,0,0,0.8)), url(${coverUrl})`, backgroundSize: 'cover', backgroundPosition: 'center' }
          : undefined
      }
    >
      {app.archived_at ? (
        <div className="absolute top-3 left-3 inline-flex items-center gap-1 rounded-full bg-white/85 px-2 py-0.5 text-[10px] font-medium text-black/75 backdrop-blur">
          已下架
        </div>
      ) : badgeLabel ? (
        <div className="absolute top-3 left-3 inline-flex items-center gap-1 rounded-full bg-white/85 px-2 py-0.5 text-[10px] font-medium text-black/75 backdrop-blur">
          {badgeLabel}
        </div>
      ) : !market && app.status === 'published' ? (
        <div className="absolute top-3 left-3 inline-flex items-center gap-1 rounded-full bg-white/85 px-2 py-0.5 text-[10px] font-medium text-black/75 backdrop-blur">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          {app.visibility === 'private' ? '私有发布' : '已发布'}
        </div>
      ) : null}
      {showMenu && (
        <div
          ref={menuRef}
          className="absolute top-2 right-2"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="p-1.5 rounded-full text-white/85 hover:bg-white/15"
            aria-label="应用菜单"
            onClick={() => setMenuOpen((v) => !v)}
          >
            <DotsIcon className="w-4 h-4" />
          </button>
          {menuOpen && (
            <div className="absolute right-0 mt-1 w-36 rounded-xl bg-white text-black text-sm shadow-card py-1 z-10">
              {market ? (
                <button
                  className="w-full text-left px-3 py-1.5 hover:bg-black/5"
                  onClick={() => {
                    setMenuOpen(false);
                    onClone?.(app.id);
                  }}
                >
                  克隆
                </button>
              ) : (
                <>
                  <button
                    className="w-full text-left px-3 py-1.5 hover:bg-black/5"
                    onClick={() => {
                      setMenuOpen(false);
                      onRequestRename?.(app);
                    }}
                  >
                    重命名
                  </button>
                  <button
                    className="w-full text-left px-3 py-1.5 text-red-600 hover:bg-red-50"
                    onClick={() => {
                      setMenuOpen(false);
                      onRequestDelete?.(app);
                    }}
                  >
                    删除
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      )}
      {busy && (
        <div className="absolute inset-x-0 top-0 p-3">
          <div className="inline-flex rounded-full bg-white/90 px-2.5 py-1 text-[11px] font-medium text-black/70 shadow-sm backdrop-blur">
            {busyLabel || '处理中...'}
          </div>
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 p-4">
        <div className="text-lg font-semibold leading-tight line-clamp-2">{app.name}</div>
        {app.description && (
          <div className="mt-1 text-xs text-white/70 line-clamp-2">{app.description}</div>
        )}
      </div>
    </div>
  );
}

export function CreateNewCard({ onClick }: { onClick(): void }) {
  return (
    <button
      onClick={onClick}
      className="w-full aspect-video rounded-card bg-neutral-200/60 border border-dashed border-black/15 hover:border-black/40 transition-colors flex items-center justify-center text-black/55"
    >
      <span className="text-sm">+ 新建应用</span>
    </button>
  );
}
