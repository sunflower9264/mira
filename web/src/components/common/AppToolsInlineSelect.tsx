import { useEffect, useMemo } from 'react';
import { useSettingsStore } from '../../stores/useSettingsStore';
import { CloudIcon } from './Icons';

interface AppToolsInlineSelectProps {
  disabledToolIds?: string[];
  onChange(disabledToolIds: string[]): void;
  className?: string;
  label?: string;
  density?: 'desktop' | 'mobile';
}

interface AppToolsSummaryProps {
  disabledToolIds?: string[];
  className?: string;
}

export function AppToolsInlineSelect({
  disabledToolIds = [],
  onChange,
  className = '',
  label = 'Tools',
  density = 'desktop',
}: AppToolsInlineSelectProps) {
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  const enabledTools = useMemo(
    () => (settings?.tools ?? []).filter((tool) => tool.enabled),
    [settings],
  );
  const disabledSet = useMemo(() => new Set(disabledToolIds), [disabledToolIds]);
  const selectedCount = enabledTools.filter((tool) => !disabledSet.has(tool.id)).length;

  const toggleTool = (toolId: string) => {
    const next = new Set(disabledToolIds);
    if (next.has(toolId)) next.delete(toolId);
    else next.add(toolId);
    onChange([...next].sort());
  };

  const chipClass =
    density === 'mobile'
      ? 'min-h-9 rounded-full px-3 py-1.5 text-xs'
      : 'min-h-8 rounded-full px-3 py-1.5 text-xs';
  const listClass =
    density === 'mobile'
      ? 'mt-2 max-h-32 overflow-y-auto'
      : 'max-h-24 overflow-y-auto';

  return (
    <div className={`min-w-0 text-xs text-black/55 ${className}`}>
      <div className="flex items-center gap-2">
        <span className="shrink-0">{label}</span>
        <span className="text-black/40">{selectedCount}/{enabledTools.length}</span>
      </div>
      {enabledTools.length === 0 ? (
        <div className="mt-1 text-xs text-black/40">暂无可用 Tools</div>
      ) : (
        <div className={`${listClass} flex flex-wrap gap-1.5 pr-1`}>
          {enabledTools.map((tool) => {
            const selected = !disabledSet.has(tool.id);
            return (
              <button
                key={tool.id}
                type="button"
                aria-pressed={selected}
                title={tool.description || tool.name}
                onClick={() => toggleTool(tool.id)}
                className={`${chipClass} inline-flex max-w-full items-center gap-1.5 border transition ${
                  selected
                    ? 'border-black/20 bg-white text-black shadow-sm hover:border-black/35'
                    : 'border-black/5 bg-black/[0.035] text-black/38 hover:text-black/60'
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    selected ? 'bg-black' : 'bg-black/20'
                  }`}
                />
                <span className="truncate">{tool.name}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function AppToolsSummary({ disabledToolIds = [], className = '' }: AppToolsSummaryProps) {
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  const enabledTools = useMemo(
    () => (settings?.tools ?? []).filter((tool) => tool.enabled),
    [settings],
  );
  const disabledSet = useMemo(() => new Set(disabledToolIds), [disabledToolIds]);
  const selectedTools = enabledTools.filter((tool) => !disabledSet.has(tool.id));
  const names = selectedTools.slice(0, 3).map((tool) => tool.name).join('、');
  const suffix = selectedTools.length > 3 ? `${names} 等 ${selectedTools.length} 个` : names;
  const text =
    enabledTools.length === 0
      ? '暂无可用 Tools'
      : selectedTools.length === 0
      ? `Tools 0/${enabledTools.length}`
      : `Tools ${selectedTools.length}/${enabledTools.length} · ${suffix}`;

  return (
    <div className={`inline-flex min-w-0 items-center gap-1.5 text-xs text-black/50 ${className}`}>
      <CloudIcon className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">{text}</span>
    </div>
  );
}
