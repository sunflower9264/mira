import { useEffect, useMemo } from 'react';
import { enabledAgentOptions, supportedModelsForAgent } from '../../lib/agentOptions';
import { useSettingsStore } from '../../stores/useSettingsStore';
import type { AppAgentKind } from '../../types';
import { SelectDropdown } from './SelectDropdown';

interface AppAgentSelectProps {
  value: AppAgentKind | undefined;
  onChange(agent: AppAgentKind, supportedModels: string[]): void;
  className?: string;
  label?: string;
}

export function AppAgentSelect({
  value,
  onChange,
  className = '',
  label = 'Agent',
}: AppAgentSelectProps) {
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  const options = useMemo(() => enabledAgentOptions(settings), [settings]);
  const selected = value ?? '';
  const disabled = options.length === 0;
  const dropdownOptions = disabled
    ? []
    : [
        { label: '选择 Agent', value: '' },
        ...options.map((option) => ({ label: option.label, value: option.value })),
      ];

  return (
    <div className={`inline-flex min-w-0 items-center gap-2 text-xs text-black/55 ${className}`}>
      <span className="shrink-0">{label}</span>
      <SelectDropdown
        value={selected}
        disabled={disabled}
        placeholder={disabled ? '无可用 Agent' : '选择 Agent'}
        emptyLabel="无可用 Agent"
        options={dropdownOptions}
        onChange={(nextValue) => {
          const next = nextValue as AppAgentKind;
          onChange(next, supportedModelsForAgent(settings, next));
        }}
      />
    </div>
  );
}
