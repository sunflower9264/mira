import { useEffect, useMemo, useState } from 'react';
import { useSettingsStore } from '../../stores/useSettingsStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { SelectDropdown } from '../common/SelectDropdown';
import { UserMenu } from '../common/UserMenu';
import { ModelChips } from './ModelChips';
import type { AgentConfigFile, AgentProviderConfig, AgentProviderId } from '../../types';

const providerLabels: Record<AgentProviderId, string> = {
  'claude-code': 'Claude Code',
  codex: 'Codex',
};

const providerHints: Record<AgentProviderId, string> = {
  'claude-code': 'settings.json',
  codex: 'config.toml + auth.json',
};

const SETTINGS_SCROLLBAR_CLASSES =
  '[scrollbar-width:thin] [scrollbar-color:rgba(0,0,0,0.18)_transparent] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-button]:hidden [&::-webkit-scrollbar-button]:h-0 [&::-webkit-scrollbar-button]:w-0 [&::-webkit-scrollbar-corner]:bg-transparent [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-black/20 [&::-webkit-resizer]:bg-transparent';
const SETTINGS_DARK_SCROLLBAR_CLASSES =
  '[scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.24)_transparent] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-button]:hidden [&::-webkit-scrollbar-button]:h-0 [&::-webkit-scrollbar-button]:w-0 [&::-webkit-scrollbar-corner]:bg-transparent [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-white/25 [&::-webkit-resizer]:bg-transparent';

interface AdminAgentOnboardingProps {
  onCompleted(): void;
}

function cloneAgents(agents: AgentProviderConfig[]) {
  return agents.map((agent) => ({
    ...agent,
    supported_models: [...(agent.supported_models ?? [])],
    status: agent.status ? { ...agent.status } : agent.status,
  }));
}

export function AdminAgentOnboarding({ onCompleted }: AdminAgentOnboardingProps) {
  const loadSettings = useSettingsStore((s) => s.load);
  const loadAgentConfig = useSettingsStore((s) => s.loadAgentConfig);
  const saveAgentConfig = useSettingsStore((s) => s.saveAgentConfig);
  const loadAgentSetupState = useSettingsStore((s) => s.loadAgentSetupState);

  const [agents, setAgents] = useState<AgentProviderConfig[]>([]);
  const [activeAgentId, setActiveAgentId] = useState<AgentProviderId>('claude-code');
  const [agentConfig, setAgentConfig] = useState<AgentConfigFile | null>(null);
  const [configDraft, setConfigDraft] = useState('');
  const [codexAuthConfig, setCodexAuthConfig] = useState<AgentConfigFile | null>(null);
  const [codexAuthDraft, setCodexAuthDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const activeAgent = useMemo(
    () => agents.find((agent) => agent.id === activeAgentId) ?? agents[0] ?? null,
    [activeAgentId, agents],
  );
  const supportedModels = useMemo(
    () => activeAgent?.supported_models ?? [],
    [activeAgent],
  );
  const canSave = !!agentConfig && !!activeAgent && (
    activeAgent.id === 'codex'
      ? configDraft.trim().length > 0 && codexAuthDraft.trim().length > 0
      : configDraft.trim().length > 0
  ) && supportedModels.length > 0;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    void loadSettings()
      .then((settings) => {
        if (cancelled) return;
        const nextAgents = cloneAgents(settings.agents);
        setAgents(nextAgents);
        setActiveAgentId(nextAgents[0]?.id ?? 'claude-code');
      })
      .catch((e) => {
        if (!cancelled) showCaughtError(e, '加载设置失败', '加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadSettings]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setAgentConfig(null);
    setConfigDraft('');
    if (activeAgentId !== 'codex') {
      setCodexAuthConfig(null);
      setCodexAuthDraft('');
    }

    const load = async () => {
      const config = await loadAgentConfig(activeAgentId);
      if (cancelled) return;
      setAgentConfig(config);
      setConfigDraft(config.content);
      if (activeAgentId === 'codex') {
        const auth = await loadAgentConfig('codex-auth');
        if (cancelled) return;
        setCodexAuthConfig(auth);
        setCodexAuthDraft(auth.content);
      }
    };

    void load()
      .catch((e) => {
        if (!cancelled) showCaughtError(e, '加载 Agent 配置失败', '加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeAgentId, loadAgentConfig]);

  function updateAgentEnabled(enabled: boolean) {
    setAgents((current) => current.map((agent) => (
      agent.id === activeAgentId ? { ...agent, enabled } : agent
    )));
  }

  function updateAgentSupportedModels(next: string[]) {
    setAgents((current) => current.map((agent) => (
      agent.id === activeAgentId
        ? { ...agent, supported_models: next }
        : agent
    )));
  }

  async function handleSave() {
    if (!activeAgent || !agentConfig || saving) return;
    if (!canSave) {
      setError(
        supportedModels.length === 0
          ? '请先填写至少一个支持模型。'
          : activeAgent.id === 'codex' ? '请先填写 config.toml 和 auth.json。' : '请先填写 settings.json。',
      );
      return;
    }
    setSaving(true);
    setError('');
    try {
      if (activeAgent.id === 'codex') {
        await saveAgentConfig('codex', configDraft, {
          enabled: activeAgent.enabled,
          authContent: codexAuthDraft,
          supportedModels,
        });
      } else {
        await saveAgentConfig('claude-code', configDraft, {
          enabled: activeAgent.enabled,
          supportedModels,
        });
      }
      const state = await loadAgentSetupState(true);
      if (state.completed) {
        onCompleted();
        return;
      }
      setError('已保存当前配置，还需要补全 Codex 的 auth.json。');
    } catch (e) {
      showCaughtError(e, '保存 Agent 配置失败', '保存失败');
    } finally {
      setSaving(false);
    }
  }

  const configLabel = activeAgent?.id === 'codex' ? 'config.toml' : 'settings.json';
  const configPlaceholder = activeAgent?.id === 'codex'
    ? 'model = "your-model-id"'
    : '{\n  "model": "your-model-id"\n}';

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[#f7f7f6]">
      <header className="h-16 shrink-0 px-6 flex items-center justify-between border-b border-black/5 bg-white/60 backdrop-blur">
        <div className="flex min-w-0 items-center gap-3">
          <span className="text-xl font-semibold tracking-tight">Mira</span>
          <span className="hidden sm:block h-5 w-px bg-black/10" />
          <span className="hidden sm:block text-sm text-black/55">Agent 初始化</span>
        </div>
        <UserMenu iconClassName="w-5 h-5" />
      </header>

      <main className="min-h-0 flex-1 overflow-hidden px-6 py-6">
        <div className="mx-auto grid h-full w-full max-w-6xl gap-5 md:grid-cols-[320px_minmax(0,1fr)]">
          <aside className={`flex min-h-0 flex-col gap-3 overflow-y-auto pr-1 ${SETTINGS_SCROLLBAR_CLASSES}`}>
            <section className="rounded-2xl bg-white p-5 ring-1 ring-black/5 shadow-card">
              <div className="text-[11px] font-medium uppercase tracking-wider text-black/55">Initial Setup</div>
              <h1 className="mt-1 text-lg font-semibold tracking-tight text-black">完成 Agent 初始化</h1>
              <p className="mt-2 text-xs leading-5 text-black/65">
                保存 Claude Code 的 settings.json，或同时保存 Codex 的 config.toml 与 auth.json，即可解锁 Mira。
              </p>
            </section>
            {agents.map((agent) => {
              const active = agent.id === activeAgentId;
              return (
                <button
                  key={agent.id}
                  type="button"
                  onClick={() => setActiveAgentId(agent.id)}
                  className={`rounded-2xl border p-4 text-left transition ${active ? 'border-black bg-black text-white shadow-card' : 'border-black/10 bg-white hover:border-black/25 hover:bg-black/[0.02]'}`}
                >
                  <div className="flex min-w-0 items-center justify-between gap-2">
                    <span className="min-w-0 truncate text-sm font-semibold">{agent.name}</span>
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${active ? 'bg-white/15 text-white/85' : 'bg-black/[0.04] text-black/55'}`}>
                      {providerHints[agent.id]}
                    </span>
                  </div>
                  <p className={`mt-2 line-clamp-2 text-xs leading-5 ${active ? 'text-white/70' : 'text-black/55'}`}>{agent.description}</p>
                </button>
              );
            })}
          </aside>

          {activeAgent ? (
            <section className="flex min-h-0 min-w-0 flex-col rounded-2xl bg-white p-5 ring-1 ring-black/5 shadow-card">
              <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-[11px] font-medium uppercase tracking-wider text-black/55">
                    {providerLabels[activeAgent.id]}
                  </div>
                  <h2 className="mt-1 truncate text-lg font-semibold tracking-tight text-black">
                    {activeAgent.name} 配置
                  </h2>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <div className="flex items-center gap-2 text-xs text-black/60">
                    <span>启用</span>
                    <SelectDropdown
                      value={activeAgent.enabled ? 'true' : 'false'}
                      disabled={loading || saving}
                      options={[
                        { label: '启用', value: 'true' },
                        { label: '禁用', value: 'false' },
                      ]}
                      onChange={(value) => updateAgentEnabled(value === 'true')}
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleSave()}
                    disabled={!canSave || loading || saving}
                    className="inline-flex h-9 items-center rounded-full bg-black px-4 text-xs font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {saving ? '保存中' : '保存并继续'}
                  </button>
                </div>
              </div>

              {error && (
                <div className="mt-3 shrink-0 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
                  {error}
                </div>
              )}

              <div className="mt-3 grid shrink-0 gap-1.5">
                <span className="text-xs font-medium text-black/60">支持模型</span>
                <ModelChips
                  values={supportedModels}
                  onChange={updateAgentSupportedModels}
                  disabled={loading || saving}
                />
              </div>

              <div className={`mt-3 grid min-h-0 flex-1 gap-3 ${activeAgent.id === 'codex' ? 'md:grid-cols-2' : 'grid-cols-1'}`}>
                <label className="flex min-h-0 min-w-0 flex-col gap-1.5">
                  <div className="flex min-w-0 items-baseline gap-2 text-xs font-medium text-black/60">
                    <span className="shrink-0">{configLabel}</span>
                    {agentConfig?.path ? (
                      <span className="min-w-0 truncate font-mono text-[10px] font-normal text-black/40">{agentConfig.path}</span>
                    ) : null}
                  </div>
                  <textarea
                    value={configDraft}
                    onChange={(event) => setConfigDraft(event.target.value)}
                    disabled={loading || saving}
                    spellCheck={false}
                    className={`min-h-0 flex-1 resize-none rounded-xl border border-black/10 bg-[#101113] px-3 py-3 font-mono text-xs leading-5 text-white outline-none transition placeholder:text-white/35 focus:border-white/20 disabled:cursor-wait disabled:opacity-60 ${SETTINGS_DARK_SCROLLBAR_CLASSES}`}
                    placeholder={loading ? '加载配置文件...' : configPlaceholder}
                  />
                </label>

                {activeAgent.id === 'codex' && (
                  <label className="flex min-h-0 min-w-0 flex-col gap-1.5">
                    <div className="flex min-w-0 items-baseline gap-2 text-xs font-medium text-black/60">
                      <span className="shrink-0">auth.json</span>
                      {codexAuthConfig?.path ? (
                        <span className="min-w-0 truncate font-mono text-[10px] font-normal text-black/40">{codexAuthConfig.path}</span>
                      ) : null}
                    </div>
                    <textarea
                      value={codexAuthDraft}
                      onChange={(event) => setCodexAuthDraft(event.target.value)}
                      disabled={loading || saving}
                      spellCheck={false}
                      className={`min-h-0 flex-1 resize-none rounded-xl border border-black/10 bg-[#101113] px-3 py-3 font-mono text-xs leading-5 text-white outline-none transition placeholder:text-white/35 focus:border-white/20 disabled:cursor-wait disabled:opacity-60 ${SETTINGS_DARK_SCROLLBAR_CLASSES}`}
                      placeholder={loading ? '加载 auth.json...' : '{\n  "OPENAI_API_KEY": "sk-..."\n}'}
                    />
                  </label>
                )}
              </div>
            </section>
          ) : (
            <section className="flex min-h-0 min-w-0 items-center justify-center rounded-2xl bg-white ring-1 ring-black/5 shadow-card text-sm text-black/45">
              {loading ? '加载 Agent 配置中…' : '暂无可配置的 Agent'}
            </section>
          )}
        </div>
      </main>
    </div>
  );
}
