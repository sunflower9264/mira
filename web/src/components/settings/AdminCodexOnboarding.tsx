import { useEffect, useState } from 'react';
import { useSettingsStore } from '../../stores/useSettingsStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { UserMenu } from '../common/UserMenu';
import { ModelChips } from './ModelChips';

const SETTINGS_SCROLLBAR_CLASSES =
  '[scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.24)_transparent] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-button]:hidden [&::-webkit-scrollbar-button]:h-0 [&::-webkit-scrollbar-button]:w-0 [&::-webkit-scrollbar-corner]:bg-transparent [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-white/25 [&::-webkit-resizer]:bg-transparent';

interface AdminCodexOnboardingProps {
  onCompleted(): void;
}

export function AdminCodexOnboarding({ onCompleted }: AdminCodexOnboardingProps) {
  const loadSettings = useSettingsStore((s) => s.load);
  const loadCodexConfig = useSettingsStore((s) => s.loadCodexConfig);
  const saveCodexConfig = useSettingsStore((s) => s.saveCodexConfig);
  const loadCodexSetupState = useSettingsStore((s) => s.loadCodexSetupState);
  const [configDraft, setConfigDraft] = useState('');
  const [authDraft, setAuthDraft] = useState('');
  const [supportedModels, setSupportedModels] = useState<string[]>([]);
  const [configPath, setConfigPath] = useState('');
  const [authPath, setAuthPath] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    void Promise.all([loadSettings(), loadCodexConfig()])
      .then(([settings, config]) => {
        if (cancelled) return;
        setSupportedModels([...settings.supported_models]);
        setConfigDraft(config.content);
        setAuthDraft(config.auth.content);
        setConfigPath(config.path);
        setAuthPath(config.auth.path);
      })
      .catch((e) => {
        if (!cancelled) showCaughtError(e, '加载 Codex 配置失败', '加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loadCodexConfig, loadSettings]);

  const canSave = configDraft.trim().length > 0 && authDraft.trim().length > 0 && supportedModels.length > 0;

  async function handleSave() {
    if (!canSave || saving) return;
    setSaving(true);
    setError('');
    try {
      await saveCodexConfig(configDraft, { authContent: authDraft, supportedModels });
      const state = await loadCodexSetupState(true);
      if (state.completed) onCompleted();
      else setError('Codex 配置尚未完成，请检查 config.toml 与 auth.json。');
    } catch (e) {
      showCaughtError(e, '保存 Codex 配置失败', '保存失败');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[#f7f7f6]">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-black/5 bg-white/60 px-6 backdrop-blur">
        <div className="flex min-w-0 items-center gap-3">
          <span className="text-xl font-semibold tracking-tight">Mira</span>
          <span className="hidden h-5 w-px bg-black/10 sm:block" />
          <span className="hidden text-sm text-black/55 sm:block">Codex 初始化</span>
        </div>
        <UserMenu iconClassName="h-5 w-5" />
      </header>

      <main className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        <section className="mx-auto w-full max-w-5xl rounded-2xl bg-white p-5 shadow-card ring-1 ring-black/5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-[11px] font-medium uppercase tracking-wider text-black/55">Initial Setup</div>
              <h1 className="mt-1 text-lg font-semibold tracking-tight text-black">完成 Codex 初始化</h1>
              <p className="mt-2 text-xs leading-5 text-black/65">保存 config.toml、auth.json 和至少一个支持模型后即可使用 Mira。</p>
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

          {error && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{error}</div>}

          <div className="mt-5 grid gap-3">
            <span className="text-xs font-medium text-black/60">支持模型</span>
            <ModelChips values={supportedModels} onChange={setSupportedModels} disabled={loading || saving} />
          </div>
          <div className="mt-5 grid gap-4 md:grid-cols-2">
            <label className="grid gap-1.5 text-xs font-medium text-black/60">
              config.toml <span className="truncate font-mono text-[11px] font-normal text-black/40">{configPath}</span>
              <textarea
                value={configDraft}
                onChange={(event) => setConfigDraft(event.target.value)}
                disabled={loading || saving}
                spellCheck={false}
                className={`min-h-[340px] resize-y rounded-xl border border-black/10 bg-[#101113] px-3 py-3 font-mono text-xs leading-5 text-white outline-none disabled:opacity-60 ${SETTINGS_SCROLLBAR_CLASSES}`}
                placeholder={'model = "your-model-id"'}
              />
            </label>
            <label className="grid gap-1.5 text-xs font-medium text-black/60">
              auth.json <span className="truncate font-mono text-[11px] font-normal text-black/40">{authPath}</span>
              <textarea
                value={authDraft}
                onChange={(event) => setAuthDraft(event.target.value)}
                disabled={loading || saving}
                spellCheck={false}
                className={`min-h-[340px] resize-y rounded-xl border border-black/10 bg-[#101113] px-3 py-3 font-mono text-xs leading-5 text-white outline-none disabled:opacity-60 ${SETTINGS_SCROLLBAR_CLASSES}`}
                placeholder={'{\n  "OPENAI_API_KEY": "sk-..."\n}'}
              />
            </label>
          </div>
        </section>
      </main>
    </div>
  );
}
