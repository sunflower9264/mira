import { useEffect, useMemo, useRef, useState } from 'react';
import type { App } from '../../types';
import { AppCard } from './AppCard';
import { SearchIcon } from '../common/Icons';

interface Props {
  items: CatalogItem[];
  onClone(id: string): void;
  onUseTemplate(id: string): void;
  importingId: string | null;
}

export interface CatalogItem {
  app: App;
  kind: 'template' | 'market';
}

export function MarketSection({ items, onClone, onUseTemplate, importingId }: Props) {
  const [q, setQ] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (searchOpen) {
      searchInputRef.current?.focus();
    }
  }, [searchOpen]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return items;
    return items.filter(
      ({ app }) => app.name.toLowerCase().includes(needle) || app.description.toLowerCase().includes(needle),
    );
  }, [items, q]);

  return (
    <section className="mt-8">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-medium">应用市场</h2>
        <div className="flex min-w-[220px] justify-end">
          {searchOpen ? (
            <div className="flex h-9 w-56 items-center gap-2 rounded-full border border-black/10 bg-white px-3 text-sm text-black/55 shadow-sm">
              <SearchIcon className="h-4 w-4 shrink-0" />
              <input
                ref={searchInputRef}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onBlur={() => setSearchOpen(false)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape' && !q) {
                    setSearchOpen(false);
                  }
                }}
                placeholder="搜索"
                className="w-full bg-transparent text-right outline-none placeholder:text-black/40"
              />
            </div>
          ) : (
            <button
              type="button"
              aria-label="搜索应用市场"
              onClick={() => setSearchOpen(true)}
              className="flex h-9 items-center gap-2 rounded-full px-3 text-sm text-black/55 transition hover:bg-black/5 hover:text-black focus:outline-none focus:ring-2 focus:ring-black/15"
            >
              <SearchIcon className="h-4 w-4 shrink-0" />
              <span>搜索</span>
            </button>
          )}
        </div>
      </div>
      <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
        {filtered.map(({ app, kind }) => (
          <AppCard
            key={`${kind}-${app.id}`}
            app={app}
            market
            hideMenu={kind === 'template'}
            badgeLabel={kind === 'template' ? '模板' : undefined}
            busy={kind === 'template' && importingId === app.id}
            busyLabel="正在导入..."
            onOpen={kind === 'template' ? () => onUseTemplate(app.id) : undefined}
            onClone={onClone}
          />
        ))}
      </div>
    </section>
  );
}
