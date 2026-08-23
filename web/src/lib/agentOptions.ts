import type { AgentKind, AppAgentKind, MiraSettings, ReasoningEffort } from '../types';

export interface AgentOption {
  label: string;
  value: AgentKind;
}

const AGENT_NAME: Record<AgentKind, string> = {
  claude: 'Claude Code',
  codex: 'Codex',
};

const CLAUDE_REASONING_EFFORTS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh', 'max'];
const CODEX_REASONING_EFFORTS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh'];
const REASONING_EFFORT_LABELS: Record<ReasoningEffort, string> = {
  low: '低 (low)',
  medium: '中 (medium)',
  high: '高 (high)',
  xhigh: '极高 (xhigh)',
  max: '最高 (max)',
};

function uniqueOptions(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

export function enabledAgentOptions(settings: MiraSettings | null): AgentOption[] {
  if (!settings) return [];
  const seen = new Set<AgentKind>();
  const options: AgentOption[] = [];
  for (const provider of settings.agents) {
    if (!provider.enabled) continue;
    if (seen.has(provider.runtime)) continue;
    seen.add(provider.runtime);
    options.push({ label: provider.name, value: provider.runtime });
  }
  return options;
}

export function supportedModelsForAgent(
  settings: MiraSettings | null,
  agent: AppAgentKind | undefined,
): string[] {
  if (!settings || !agent) return [];
  return uniqueOptions(
    settings.agents
      .filter((provider) => provider.enabled && provider.runtime === agent)
      .flatMap((provider) => provider.supported_models ?? []),
  );
}

export function isAgentEnabled(
  settings: MiraSettings | null,
  agent: AppAgentKind | undefined,
): boolean {
  if (!settings || !agent) return false;
  return settings.agents.some((provider) => provider.enabled && provider.runtime === agent);
}

export function agentName(agent: AppAgentKind | undefined): string {
  return agent ? AGENT_NAME[agent] : '未选择 Agent';
}

function reasoningEffortsForAgent(agent: AppAgentKind | undefined): ReasoningEffort[] {
  if (agent === 'claude') return CLAUDE_REASONING_EFFORTS;
  if (agent === 'codex') return CODEX_REASONING_EFFORTS;
  return [];
}

export function reasoningEffortOptionsForAgent(
  agent: AppAgentKind | undefined,
): { label: string; value: ReasoningEffort }[] {
  return reasoningEffortsForAgent(agent).map((effort) => ({
    label: REASONING_EFFORT_LABELS[effort],
    value: effort,
  }));
}

export function defaultReasoningEffortForAgent(agent: AppAgentKind | undefined): ReasoningEffort | undefined {
  return reasoningEffortsForAgent(agent)[0];
}

export function normalizeReasoningEffortForAgent(
  agent: AppAgentKind | undefined,
  effort: string | undefined,
): ReasoningEffort | undefined {
  const efforts = reasoningEffortsForAgent(agent);
  if (efforts.length === 0) return undefined;
  return efforts.includes(effort as ReasoningEffort) ? (effort as ReasoningEffort) : efforts[0];
}
