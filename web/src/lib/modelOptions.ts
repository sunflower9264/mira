import type { MiraSettings, ReasoningEffort } from '../types';

const CODEX_REASONING_EFFORTS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh'];
const REASONING_EFFORT_LABELS: Record<ReasoningEffort, string> = {
  low: '低 (low)',
  medium: '中 (medium)',
  high: '高 (high)',
  xhigh: '极高 (xhigh)',
};

export function supportedModels(settings: MiraSettings | null): string[] {
  if (!settings) return [];
  return Array.from(new Set(settings.supported_models.map((value) => value.trim()).filter(Boolean)));
}

export function reasoningEffortOptions(): { label: string; value: ReasoningEffort }[] {
  return CODEX_REASONING_EFFORTS.map((effort) => ({
    label: REASONING_EFFORT_LABELS[effort],
    value: effort,
  }));
}

export function defaultReasoningEffort(): ReasoningEffort {
  return CODEX_REASONING_EFFORTS[0];
}

export function normalizeReasoningEffort(effort: string | undefined): ReasoningEffort {
  return CODEX_REASONING_EFFORTS.includes(effort as ReasoningEffort)
    ? effort as ReasoningEffort
    : defaultReasoningEffort();
}
