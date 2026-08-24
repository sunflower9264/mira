import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AppDialog } from '../common/AppDialog';
import { EditIcon, PlusIcon, RefreshIcon, SettingsIcon } from '../common/Icons';
import { useSettingsStore } from '../../stores/useSettingsStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { ModelChips } from './ModelChips';
import { getSkillMarkdown } from '../../lib/api';
import type {
  CodexConfigFile,
  CodexStatus,
  InstructionFile,
  McpHeader,
  McpServerConfig,
  MiraSettings,
  PromptTemplate,
  SkillConfig,
  SkillMarkdown,
} from '../../types';

type SettingsCategory = 'codex' | 'instructions' | 'prompts' | 'skills' | 'mcp';
type McpDraft = Pick<McpServerConfig, 'name' | 'url' | 'headers' | 'env_var_names' | 'planning_enabled'>;
type PendingMcpServer = McpServerConfig;
type McpFormMode = 'pending' | 'editing';

const categories: Array<{ id: SettingsCategory; label: string; description: string }> = [
  { id: 'codex', label: 'Codex 设置', description: 'App Server' },
  { id: 'instructions', label: '全局指令', description: 'AGENTS.md' },
  { id: 'prompts', label: '提示词管理', description: 'Prompt Seed' },
  { id: 'skills', label: 'Skills', description: '全局技能包' },
  { id: 'mcp', label: 'MCP', description: 'HTTP Server 清单' },
];

const SETTINGS_SCROLLBAR_CLASSES =
  '[scrollbar-width:thin] [scrollbar-color:rgba(0,0,0,0.18)_transparent] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-button]:hidden [&::-webkit-scrollbar-button]:h-0 [&::-webkit-scrollbar-button]:w-0 [&::-webkit-scrollbar-corner]:bg-transparent [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-black/20 [&::-webkit-resizer]:bg-transparent';
const SETTINGS_DARK_SCROLLBAR_CLASSES =
  '[scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.24)_transparent] [&::-webkit-scrollbar]:h-1.5 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-button]:hidden [&::-webkit-scrollbar-button]:h-0 [&::-webkit-scrollbar-button]:w-0 [&::-webkit-scrollbar-corner]:bg-transparent [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-white/25 [&::-webkit-resizer]:bg-transparent';

interface SettingsDialogProps {
  open: boolean;
  onClose(): void;
}

function cloneSettings(settings: MiraSettings): MiraSettings {
  return {
    supported_models: [...settings.supported_models],
    skills: settings.skills.map((skill) => ({ ...skill })),
    mcp_servers: settings.mcp_servers.map((server) => ({
      ...server,
      planning_enabled: !!server.planning_enabled,
      headers: server.headers.map((header) => ({ ...header })),
      env_var_names: [...server.env_var_names],
    })),
    tools: settings.tools.map((tool) => ({ ...tool })),
  };
}

function createMcpDraft(): McpDraft {
  return {
    name: '',
    url: '',
    planning_enabled: false,
    headers: [{ name: '', value: '' }],
    env_var_names: [''],
  };
}

function createConfigId(prefix: string) {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

function createPendingMcpServer(): PendingMcpServer {
  return {
    id: createConfigId('mcp'),
    enabled: true,
    ...createMcpDraft(),
  };
}

function cloneMcpServer(server: McpServerConfig): McpServerConfig {
  return {
    ...server,
    planning_enabled: !!server.planning_enabled,
    headers: server.headers.map((header) => ({ ...header })),
    env_var_names: [...server.env_var_names],
  };
}

function compactStrings(values: string[]) {
  return values.map((value) => value.trim()).filter(Boolean);
}

function compactHeaders(values: McpHeader[]) {
  return values
    .map((header) => ({ name: header.name.trim(), value: header.value.trim() }))
    .filter((header) => header.name.length > 0);
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function getSkillDependencyBadge(skill: SkillConfig) {
  if (skill.dependency_status === 'ready') {
    return { label: '依赖就绪', className: 'bg-emerald-50 text-emerald-700' };
  }
  if (skill.dependency_status === 'failed') {
    return { label: '依赖失败', className: 'bg-red-50 text-red-700' };
  }
  if (skill.dependency_status === 'pending') {
    return { label: '等待构建', className: 'bg-amber-50 text-amber-700' };
  }
  return { label: '无需依赖', className: 'bg-black/5 text-black/50' };
}

function isSkillDependencyBlocked(skill: SkillConfig) {
  return skill.dependency_status === 'pending' || skill.dependency_status === 'failed';
}

function getSkillDependencyBlockedMessage(skill: SkillConfig) {
  return skill.dependency_status === 'pending'
    ? '依赖构建完成后才能启用'
    : '请修复依赖构建错误后再启用';
}

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作失败，请稍后重试';
}

export function SettingsDialog({ open, onClose }: SettingsDialogProps) {
  const loadSettings = useSettingsStore((s) => s.load);
  const updateSkillEnabled = useSettingsStore((s) => s.updateSkillEnabled);
  const updateSkillPlanningEnabled = useSettingsStore((s) => s.updateSkillPlanningEnabled);
  const deleteSkillAction = useSettingsStore((s) => s.deleteSkill);
  const addMcpServerAction = useSettingsStore((s) => s.addMcpServer);
  const updateMcpServerAction = useSettingsStore((s) => s.updateMcpServer);
  const deleteMcpServerAction = useSettingsStore((s) => s.deleteMcpServer);
  const loadCodexConfig = useSettingsStore((s) => s.loadCodexConfig);
  const saveCodexConfig = useSettingsStore((s) => s.saveCodexConfig);
  const loadInstructionFile = useSettingsStore((s) => s.loadInstructionFile);
  const saveInstructionFile = useSettingsStore((s) => s.saveInstructionFile);
  const loadPromptTemplates = useSettingsStore((s) => s.loadPromptTemplates);
  const savePromptTemplate = useSettingsStore((s) => s.savePromptTemplate);
  const parseSkillArchive = useSettingsStore((s) => s.parseSkillArchive);
  const refreshCodexStatus = useSettingsStore((s) => s.refreshCodexStatus);
  const loading = useSettingsStore((s) => s.loading);
  const saving = useSettingsStore((s) => s.saving);

  const [activeCategory, setActiveCategory] = useState<SettingsCategory>('codex');
  const [draft, setDraft] = useState<MiraSettings | null>(null);
  const [error, setError] = useState('');
  const [codexConfig, setCodexConfig] = useState<CodexConfigFile | null>(null);
  const [configDraft, setConfigDraft] = useState('');
  const [isLoadingConfig, setIsLoadingConfig] = useState(false);
  const [isSavingConfig, setIsSavingConfig] = useState(false);
  const [codexAuthDraft, setCodexAuthDraft] = useState('');
  const [instruction, setInstruction] = useState<InstructionFile | null>(null);
  const [instructionDraft, setInstructionDraft] = useState('');
  const [isLoadingInstructions, setIsLoadingInstructions] = useState(false);
  const [isSavingInstructions, setIsSavingInstructions] = useState(false);
  const [prompts, setPrompts] = useState<PromptTemplate[]>([]);
  const [promptDrafts, setPromptDrafts] = useState<Record<string, string>>({});
  const [isLoadingPrompts, setIsLoadingPrompts] = useState(false);
  const [savingPromptKey, setSavingPromptKey] = useState<string | null>(null);
  const [editingPrompt, setEditingPrompt] = useState<PromptTemplate | null>(null);
  const [editingPromptDraft, setEditingPromptDraft] = useState('');
  const [isParsingSkill, setIsParsingSkill] = useState(false);
  const [previewingSkill, setPreviewingSkill] = useState<SkillConfig | null>(null);
  const [skillMarkdown, setSkillMarkdown] = useState<SkillMarkdown | null>(null);
  const [isLoadingSkillMarkdown, setIsLoadingSkillMarkdown] = useState(false);
  const [skillMarkdownError, setSkillMarkdownError] = useState('');
  const [isRefreshingStatus, setIsRefreshingStatus] = useState(false);
  const [codexStatus, setCodexStatus] = useState<CodexStatus | null>(null);
  const [pendingMcpServers, setPendingMcpServers] = useState<PendingMcpServer[]>([]);
  const [editingMcpServers, setEditingMcpServers] = useState<Record<string, McpServerConfig>>({});
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const didAutoRefreshStatusesRef = useRef(false);

  async function handleRefreshCodexStatus() {
    setIsRefreshingStatus(true);
    setError('');
    try {
      setCodexStatus(await refreshCodexStatus());
    } catch (e) {
      showCaughtError(e, '刷新 Codex 状态失败', '刷新失败');
    } finally {
      setIsRefreshingStatus(false);
    }
  }

  useEffect(() => {
    if (!open) {
      didAutoRefreshStatusesRef.current = false;
      setDraft(null);
      setEditingPrompt(null);
      setEditingPromptDraft('');
      return;
    }
    didAutoRefreshStatusesRef.current = false;
    setActiveCategory('codex');
    setError('');
    void loadSettings()
      .then((settings) => {
        setDraft(cloneSettings(settings));
        setInstruction(null);
        setInstructionDraft('');
        setPrompts([]);
        setPromptDrafts({});
        setEditingPrompt(null);
        setEditingPromptDraft('');
        setPendingMcpServers([]);
        setEditingMcpServers({});
      })
      .catch((e) => showCaughtError(e, '加载设置失败', '加载失败'));
  }, [loadSettings, open]);

  useEffect(() => {
    if (!open || activeCategory !== 'codex' || !draft || didAutoRefreshStatusesRef.current) return;
    didAutoRefreshStatusesRef.current = true;
    void handleRefreshCodexStatus();
  }, [activeCategory, draft, open]);

  useEffect(() => {
    if (!open || activeCategory !== 'codex') return;
    setIsLoadingConfig(true);
    setError('');
    void loadCodexConfig()
      .then((config) => {
        setCodexConfig(config);
        setConfigDraft(config.content);
        setCodexAuthDraft(config.auth.content);
      })
      .catch((e) => showCaughtError(e, '加载 Codex 配置失败', '加载失败'))
      .finally(() => setIsLoadingConfig(false));
  }, [activeCategory, loadCodexConfig, open]);

  useEffect(() => {
    if (!open || activeCategory !== 'instructions') return;
    setIsLoadingInstructions(true);
    setError('');
    void loadInstructionFile()
      .then((file) => {
        setInstruction(file);
        setInstructionDraft(file.content);
      })
      .catch((e) => showCaughtError(e, '加载 Instructions 失败', '加载失败'))
      .finally(() => setIsLoadingInstructions(false));
  }, [activeCategory, loadInstructionFile, open]);

  useEffect(() => {
    if (!open || activeCategory !== 'prompts') return;
    setIsLoadingPrompts(true);
    setError('');
    void loadPromptTemplates()
      .then((items) => {
        setPrompts(items);
        setPromptDrafts(Object.fromEntries(items.map((item) => [item.key, item.content])));
      })
      .catch((e) => showCaughtError(e, '加载 Prompt Templates 失败', '加载失败'))
      .finally(() => setIsLoadingPrompts(false));
  }, [activeCategory, loadPromptTemplates, open]);

  async function removeSkill(skillId: string) {
    setError('');
    try {
      await deleteSkillAction(skillId);
      if (previewingSkill?.id === skillId) closeSkillPreview();
      setDraft((current) => current ? {
        ...current,
        skills: current.skills.filter((skill) => skill.id !== skillId),
      } : current);
    } catch (e) {
      showCaughtError(e, '删除 Skill 失败', '删除失败');
    }
  }

  async function openSkillPreview(skill: SkillConfig) {
    setPreviewingSkill(skill);
    setSkillMarkdown(null);
    setSkillMarkdownError('');
    setIsLoadingSkillMarkdown(true);
    try {
      const markdown = await getSkillMarkdown(skill.id);
      setSkillMarkdown(markdown);
    } catch (e) {
      setSkillMarkdownError(getErrorMessage(e));
    } finally {
      setIsLoadingSkillMarkdown(false);
    }
  }

  function closeSkillPreview() {
    setPreviewingSkill(null);
    setSkillMarkdown(null);
    setSkillMarkdownError('');
    setIsLoadingSkillMarkdown(false);
  }

  async function removeMcpServer(serverId: string) {
    setError('');
    try {
      await deleteMcpServerAction(serverId);
      setDraft((current) => current ? {
        ...current,
        mcp_servers: current.mcp_servers.filter((server) => server.id !== serverId),
      } : current);
      cancelEditingMcpServer(serverId);
    } catch (e) {
      showCaughtError(e, '删除 MCP 失败', '删除失败');
    }
  }

  async function handleSaveCodexConfig() {
    if (!codexConfig || !draft) return;
    if (draft.supported_models.length === 0) {
      setError('请先填写至少一个支持模型。');
      return;
    }
    setIsSavingConfig(true);
    setError('');
    try {
      const saved = await saveCodexConfig(configDraft, {
        authContent: codexAuthDraft,
        supportedModels: draft.supported_models,
      });
      setCodexConfig(saved);
      setConfigDraft(saved.content);
      setCodexAuthDraft(saved.auth.content);
      if (saved.settings) setDraft(cloneSettings(saved.settings));
      void handleRefreshCodexStatus();
    } catch (e) {
      showCaughtError(e, '保存 Codex 配置失败', '保存失败');
    } finally {
      setIsSavingConfig(false);
    }
  }

  async function handleSaveInstructions(closeAfterSave = false) {
    setIsSavingInstructions(true);
    setError('');
    try {
      const saved = await saveInstructionFile(instructionDraft);
      setInstruction(saved);
      setInstructionDraft(saved.content);
      if (closeAfterSave) onClose();
    } catch (e) {
      showCaughtError(e, '保存 Instructions 失败', '保存失败');
    } finally {
      setIsSavingInstructions(false);
    }
  }

  function openPromptEditor(prompt: PromptTemplate) {
    setEditingPrompt(prompt);
    setEditingPromptDraft(promptDrafts[prompt.key] ?? prompt.content);
  }

  function closePromptEditor() {
    if (savingPromptKey !== null) return;
    setEditingPrompt(null);
    setEditingPromptDraft('');
  }

  async function handleSavePrompt(prompt: PromptTemplate, content: string) {
    setSavingPromptKey(prompt.key);
    setError('');
    try {
      const saved = await savePromptTemplate(prompt.key, content);
      setPrompts((current) => current.map((item) => item.key === saved.key ? saved : item));
      setPromptDrafts((current) => ({ ...current, [saved.key]: saved.content }));
      setEditingPrompt(null);
      setEditingPromptDraft('');
    } catch (e) {
      showCaughtError(e, '保存 Prompt Template 失败', '保存失败');
    } finally {
      setSavingPromptKey(null);
    }
  }

  async function handleSkillArchiveUpload(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.zip')) {
      setError('Skill 上传只接受 .zip 压缩包。');
      return;
    }
    setIsParsingSkill(true);
    setError('');
    try {
      const skill = await parseSkillArchive(file);
      setDraft((current) => current ? {
        ...current,
        skills: [skill, ...current.skills],
      } : current);
    } catch (e) {
      showCaughtError(e, '上传 Skill 失败', '上传失败');
    } finally {
      setIsParsingSkill(false);
    }
  }

  function addPendingMcpServer() {
    setError('');
    setPendingMcpServers((current) => [createPendingMcpServer(), ...current]);
  }

  function removePendingMcpServer(serverId: string) {
    setPendingMcpServers((current) => current.filter((server) => server.id !== serverId));
  }

  function updateMcpFormServer(
    mode: McpFormMode,
    serverId: string,
    updater: (server: McpServerConfig) => McpServerConfig,
  ) {
    if (mode === 'pending') {
      setPendingMcpServers((current) => current.map((server) => (
        server.id === serverId ? updater(server) : server
      )));
      return;
    }
    setEditingMcpServers((current) => {
      const server = current[serverId];
      if (!server) return current;
      return { ...current, [serverId]: updater(server) };
    });
  }

  function patchMcpFormServer(mode: McpFormMode, serverId: string, patch: Partial<McpDraft>) {
    updateMcpFormServer(mode, serverId, (server) => ({ ...server, ...patch }));
  }

  function updateMcpFormHeader(mode: McpFormMode, serverId: string, index: number, patch: Partial<McpHeader>) {
    updateMcpFormServer(mode, serverId, (server) => ({
      ...server,
      headers: server.headers.map((header, headerIndex) => (
        headerIndex === index ? { ...header, ...patch } : header
      )),
    }));
  }

  function updateMcpFormEnvVarName(mode: McpFormMode, serverId: string, index: number, value: string) {
    updateMcpFormServer(mode, serverId, (server) => ({
      ...server,
      env_var_names: server.env_var_names.map((name, nameIndex) => nameIndex === index ? value : name),
    }));
  }

  function buildMcpServerPayload(server: McpServerConfig): McpServerConfig | null {
    const name = server.name.trim();
    const url = server.url.trim();
    if (!name) {
      setError('请先填写 MCP 名称。');
      return null;
    }
    if (!url) {
      setError('请先填写 HTTP MCP URL。');
      return null;
    }
    return {
      ...server,
      name,
      url,
      headers: compactHeaders(server.headers),
      env_var_names: compactStrings(server.env_var_names),
    };
  }

  async function savePendingMcpServer(pendingServer: PendingMcpServer) {
    const server = buildMcpServerPayload(pendingServer);
    if (!server) return;
    setError('');
    try {
      const saved = await addMcpServerAction(server);
      setDraft((current) => current ? { ...current, mcp_servers: cloneSettings(saved).mcp_servers } : cloneSettings(saved));
      removePendingMcpServer(pendingServer.id);
    } catch (e) {
      showCaughtError(e, '新增 MCP 失败', '新增失败');
    }
  }

  function startEditingMcpServer(server: McpServerConfig) {
    setError('');
    setEditingMcpServers((current) => ({ ...current, [server.id]: cloneMcpServer(server) }));
  }

  function cancelEditingMcpServer(serverId: string) {
    setEditingMcpServers((current) => {
      const next = { ...current };
      delete next[serverId];
      return next;
    });
  }

  async function saveEditingMcpServer(editingServer: McpServerConfig) {
    const server = buildMcpServerPayload(editingServer);
    if (!server) return;
    setError('');
    try {
      const saved = await updateMcpServerAction(server.id, server);
      setDraft((current) => current ? { ...current, mcp_servers: cloneSettings(saved).mcp_servers } : cloneSettings(saved));
      cancelEditingMcpServer(server.id);
    } catch (e) {
      showCaughtError(e, '保存 MCP 失败', '保存失败');
    }
  }

  async function toggleSkillEnabled(skill: SkillConfig) {
    setError('');
    try {
      const saved = await updateSkillEnabled(skill.id, !skill.enabled);
      setDraft((current) => current ? { ...current, skills: cloneSettings(saved).skills } : cloneSettings(saved));
    } catch (e) {
      showCaughtError(e, '更新 Skill 失败', '更新失败');
    }
  }

  async function toggleSkillPlanningEnabled(skill: SkillConfig) {
    setError('');
    try {
      const saved = await updateSkillPlanningEnabled(skill.id, !skill.planning_enabled);
      setDraft((current) => current ? { ...current, skills: cloneSettings(saved).skills } : cloneSettings(saved));
    } catch (e) {
      showCaughtError(e, '更新 Skill 失败', '更新失败');
    }
  }

  async function toggleMcpServerEnabled(server: McpServerConfig) {
    setError('');
    try {
      const saved = await updateMcpServerAction(server.id, { ...server, enabled: !server.enabled });
      setDraft((current) => current ? { ...current, mcp_servers: cloneSettings(saved).mcp_servers } : cloneSettings(saved));
    } catch (e) {
      showCaughtError(e, '更新 MCP 失败', '更新失败');
    }
  }

  async function toggleMcpServerPlanningEnabled(server: McpServerConfig) {
    setError('');
    try {
      const saved = await updateMcpServerAction(server.id, { ...server, planning_enabled: !server.planning_enabled });
      setDraft((current) => current ? { ...current, mcp_servers: cloneSettings(saved).mcp_servers } : cloneSettings(saved));
    } catch (e) {
      showCaughtError(e, '更新 MCP 失败', '更新失败');
    }
  }

  function renderInstructionSettings() {
    return (
      <div className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wider text-black/45">Global Instructions</div>
            <h4 className="mt-1 text-xl font-semibold tracking-tight text-black">AGENTS.md</h4>
          </div>
          <button
            type="button"
            onClick={() => void handleSaveInstructions()}
            disabled={isSavingInstructions || isLoadingInstructions}
            className="inline-flex h-10 items-center rounded-full bg-black px-4 text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSavingInstructions ? '保存中' : '保存全局指令'}
          </button>
        </div>
        <section className="rounded-2xl border border-black/10 bg-white p-4">
          <div className="text-sm font-semibold text-black">Codex</div>
          <div className="mt-1 font-mono text-xs text-black/50">AGENTS.md</div>
          <div className="mt-3 grid gap-1.5 text-xs font-medium text-black/60">
            文件路径
            <div className="flex h-10 min-w-0 items-center rounded-xl border border-black/10 bg-black/[0.02] px-3 font-mono text-[11px] text-black/60">
              <span className="truncate">{instruction?.path ?? (isLoadingInstructions ? '加载中...' : '')}</span>
            </div>
          </div>
          <label className="mt-3 grid gap-1.5 text-xs font-medium text-black/60">
            AGENTS.md
            <textarea
              value={instructionDraft}
              onChange={(event) => setInstructionDraft(event.target.value)}
              disabled={isLoadingInstructions}
              spellCheck={false}
              className={`min-h-[220px] resize-y rounded-xl border border-black/10 bg-[#101113] px-3 py-3 font-mono text-xs leading-5 text-white outline-none transition placeholder:text-white/35 focus:border-black/30 disabled:cursor-wait disabled:opacity-60 ${SETTINGS_DARK_SCROLLBAR_CLASSES}`}
              placeholder={isLoadingInstructions ? '加载指令文件...' : 'AGENTS.md plain text'}
            />
          </label>
        </section>
      </div>
    );
  }

  function renderPromptSettings() {
    return (
      <div className="space-y-5">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wider text-black/45">Prompt Templates</div>
          <h4 className="mt-1 text-xl font-semibold tracking-tight text-black">运行时提示词</h4>
          <p className="mt-2 text-xs leading-5 text-black/55">保存会同时更新数据库和 seed 文件。</p>
        </div>
        <div className="grid gap-4">
          {prompts.map((prompt) => {
            const savingThis = savingPromptKey === prompt.key;
            const variables = prompt.variables.length ? prompt.variables.map((item) => `$${item}`).join(' / ') : '无变量';
            return (
              <section key={prompt.key} className="rounded-2xl border border-black/10 bg-white p-4">
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold text-black">{prompt.name}</span>
                      <span className="rounded-full bg-black/[0.04] px-2.5 py-1 font-mono text-[11px] text-black/55">
                        {prompt.key}
                      </span>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-black/55">{prompt.description}</p>
                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-black/55">
                      <span className="rounded-full border border-black/10 bg-black/[0.02] px-2 py-1 font-mono">{variables}</span>
                      <span className="rounded-full border border-black/10 bg-black/[0.02] px-2 py-1">
                        {formatDate(prompt.updated_at)}
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <button
                      type="button"
                      onClick={() => openPromptEditor(prompt)}
                      disabled={savingPromptKey !== null || isLoadingPrompts}
                      className="inline-flex h-9 items-center gap-2 rounded-full border border-black/10 px-3 text-xs font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <EditIcon className="h-3.5 w-3.5" />
                      {savingThis ? '保存中' : '编辑'}
                    </button>
                  </div>
                </div>
              </section>
            );
          })}
        </div>
        {prompts.length === 0 && (
          <div className="rounded-2xl border border-dashed border-black/15 bg-black/[0.02] px-3 py-10 text-center text-sm text-black/45">
            {isLoadingPrompts ? '加载提示词中…' : '暂无提示词'}
          </div>
        )}
      </div>
    );
  }

  function renderCodexSettings() {
    if (!draft) return null;
    const enabledSkillCount = draft.skills.filter((skill) => skill.enabled).length;
    const enabledMcpCount = draft.mcp_servers.filter((server) => server.enabled).length;

    return (
      <div className="space-y-5">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-wider text-black/45">Codex 设置</div>
          <h4 className="mt-1 text-xl font-semibold tracking-tight text-black">Codex App Server</h4>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          {(() => {
            const status = codexStatus;
            const available = !!status && status.installed && status.runnable === true;
            const label = !status
              ? '未检测'
              : !status.installed
                ? '未安装'
                : status.runnable === true
                  ? '可用'
                  : status.runnable === false
                    ? '不可用'
                    : '未检测';
            const tooltip = status?.error ?? (available ? 'Codex 已就绪' : 'Codex 状态');
            return (
              <div className="rounded-2xl border border-black/10 bg-black/[0.02] p-3" title={tooltip}>
                <div className="flex items-center justify-between gap-2">
                  <div className="text-xs text-black/50">当前状态</div>
                  <button
                    type="button"
                    onClick={() => void handleRefreshCodexStatus()}
                    disabled={isRefreshingStatus}
                    title="刷新状态"
                    aria-label="刷新状态"
                    className="-m-1 inline-flex h-6 w-6 items-center justify-center rounded-full text-black/55 transition hover:bg-black/10 hover:text-black disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <RefreshIcon className={`h-3.5 w-3.5 ${isRefreshingStatus ? 'animate-spin' : ''}`} />
                  </button>
                </div>
                <div className={`mt-1 truncate text-sm font-semibold ${available ? 'text-emerald-600' : status ? 'text-rose-600' : 'text-black/60'}`}>
                  {label}
                </div>
                <div className="mt-0.5 truncate text-[11px] text-black/45">{status?.identity || '—'}</div>
              </div>
            );
          })()}
          <div className="rounded-2xl border border-black/10 bg-black/[0.02] p-3">
            <div className="text-xs text-black/50">启用 Skills</div>
            <div className="mt-1 text-sm font-semibold text-black">{enabledSkillCount} / {draft.skills.length}</div>
          </div>
          <div className="rounded-2xl border border-black/10 bg-black/[0.02] p-3">
            <div className="text-xs text-black/50">启用 MCP</div>
            <div className="mt-1 text-sm font-semibold text-black">{enabledMcpCount} / {draft.mcp_servers.length}</div>
          </div>
        </div>
        <section className="rounded-2xl border border-black/10 bg-white p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-black">
              <SettingsIcon className="h-4 w-4" />
              Codex 配置文件
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void handleSaveCodexConfig()}
                disabled={!codexConfig || isSavingConfig || isLoadingConfig || draft.supported_models.length === 0}
                className="inline-flex h-8 items-center rounded-full bg-black px-3 text-xs font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isSavingConfig ? '保存中' : '保存配置文件'}
              </button>
            </div>
          </div>
          <div className="mt-4 grid gap-3">
            <div className="grid gap-1.5 text-xs font-medium text-black/60">
              文件路径
              <div className="flex h-10 min-w-0 items-center rounded-xl border border-black/10 bg-black/[0.02] px-3 font-mono text-[11px] text-black/60">
                <span className="truncate">{codexConfig?.path ?? (isLoadingConfig ? '加载中...' : '')}</span>
              </div>
            </div>
          </div>
          <div className="mt-3 grid gap-1.5">
            <span className="text-xs font-medium text-black/60">支持模型</span>
            <ModelChips
              values={draft.supported_models}
              onChange={(next) => setDraft((current) => current ? { ...current, supported_models: next } : current)}
              disabled={isLoadingConfig || isSavingConfig}
            />
          </div>
          <label className="mt-3 grid gap-1.5 text-xs font-medium text-black/60">
            config.toml
            <textarea
              value={configDraft}
              onChange={(event) => setConfigDraft(event.target.value)}
              disabled={isLoadingConfig}
              spellCheck={false}
              className={`min-h-[260px] resize-y rounded-xl border border-black/10 bg-[#101113] px-3 py-3 font-mono text-xs leading-5 text-white outline-none transition placeholder:text-white/35 focus:border-black/30 disabled:cursor-wait disabled:opacity-60 ${SETTINGS_DARK_SCROLLBAR_CLASSES}`}
              placeholder={isLoadingConfig ? '加载配置文件...' : 'model = "your-model-id"'}
            />
          </label>
          <div className="mt-4 grid gap-1.5 text-xs font-medium text-black/60">
            auth.json 文件路径
            <div className="flex h-10 min-w-0 items-center rounded-xl border border-black/10 bg-black/[0.02] px-3 font-mono text-[11px] text-black/60">
              <span className="truncate">{codexConfig?.auth.path ?? (isLoadingConfig ? '加载中...' : '')}</span>
            </div>
          </div>
          <label className="mt-3 grid gap-1.5 text-xs font-medium text-black/60">
            auth.json
            <textarea
              value={codexAuthDraft}
              onChange={(event) => setCodexAuthDraft(event.target.value)}
              disabled={isLoadingConfig}
              spellCheck={false}
              className={`min-h-[180px] resize-y rounded-xl border border-black/10 bg-[#101113] px-3 py-3 font-mono text-xs leading-5 text-white outline-none transition placeholder:text-white/35 focus:border-black/30 disabled:cursor-wait disabled:opacity-60 ${SETTINGS_DARK_SCROLLBAR_CLASSES}`}
              placeholder={isLoadingConfig ? '加载 auth.json...' : '{\n  "OPENAI_API_KEY": "sk-..."\n}'}
            />
          </label>
        </section>
      </div>
    );
  }

  function renderSkillsSettings() {
    if (!draft) return null;
    return (
      <div className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wider text-black/45">Global Skills</div>
            <h4 className="mt-1 text-xl font-semibold tracking-tight text-black">Skills</h4>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip,application/x-zip-compressed"
              className="hidden"
              onChange={(event) => {
                void handleSkillArchiveUpload(event.target.files);
                event.target.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isParsingSkill}
              className="inline-flex h-10 items-center gap-2 rounded-full bg-black px-4 text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <PlusIcon className="h-4 w-4" />
              {isParsingSkill ? '解析中' : '上传 Skill'}
            </button>
          </div>
        </div>
        <div className="grid gap-3">
          {draft.skills.map((skill) => (
            <section key={skill.id} className="rounded-2xl border border-black/10 bg-white p-4">
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                <div className="min-w-0">
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <span className="min-w-0 truncate text-sm font-semibold text-black">{skill.name}</span>
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${skill.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-black/5 text-black/50'}`}>
                      {skill.enabled ? '已启用' : '已禁用'}
                    </span>
                    {skill.planning_enabled && (
                      <span className="shrink-0 rounded-full bg-sky-50 px-2 py-0.5 text-[11px] text-sky-700">规划可用</span>
                    )}
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${getSkillDependencyBadge(skill).className}`}>
                      {getSkillDependencyBadge(skill).label}
                    </span>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-black/55">
                    <span className="max-w-full truncate rounded-full border border-black/10 bg-black/[0.02] px-2 py-1 font-mono">{skill.archive_name}</span>
                    <span className="rounded-full border border-black/10 bg-black/[0.02] px-2 py-1">{formatBytes(skill.archive_size)}</span>
                    <span className="rounded-full border border-black/10 bg-black/[0.02] px-2 py-1">{formatDate(skill.uploaded_at)}</span>
                  </div>
                  {skill.dependency_status === 'failed' && skill.dependency_error && (
                    <div className="mt-2 rounded-lg border border-red-100 bg-red-50/70 px-2.5 py-2 text-xs leading-5 text-red-700">
                      {skill.dependency_error}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">
                  <button
                    type="button"
                    onClick={() => void openSkillPreview(skill)}
                    disabled={saving || isParsingSkill}
                    className="h-9 rounded-full border border-black/10 px-3 text-xs font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    预览
                  </button>
                  <button
                    type="button"
                    onClick={() => void toggleSkillEnabled(skill)}
                    disabled={saving || isParsingSkill || (!skill.enabled && isSkillDependencyBlocked(skill))}
                    title={!skill.enabled && isSkillDependencyBlocked(skill) ? getSkillDependencyBlockedMessage(skill) : undefined}
                    className="h-9 rounded-full border border-black/10 px-3 text-xs font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {skill.enabled ? '禁用' : '启用'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void toggleSkillPlanningEnabled(skill)}
                    disabled={saving || isParsingSkill || (!skill.planning_enabled && isSkillDependencyBlocked(skill))}
                    title={!skill.planning_enabled && isSkillDependencyBlocked(skill) ? getSkillDependencyBlockedMessage(skill) : undefined}
                    className="h-9 rounded-full border border-black/10 px-3 text-xs font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {skill.planning_enabled ? '取消规划可用' : '设为规划可用'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void removeSkill(skill.id)}
                    disabled={saving || isParsingSkill}
                    className="h-9 rounded-full border border-red-200 px-3 text-xs font-medium text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    删除
                  </button>
                </div>
              </div>
            </section>
          ))}
        </div>
        {draft.skills.length === 0 && (
          <div className="rounded-2xl border border-dashed border-black/15 bg-black/[0.02] px-3 py-10 text-center text-sm text-black/45">
            暂无全局 Skill，请上传 .zip 压缩包。
          </div>
        )}
      </div>
    );
  }

  function renderMcpFormCard(server: McpServerConfig, mode: McpFormMode) {
    const isEditing = mode === 'editing';
    return (
      <section key={server.id} className={`rounded-2xl border p-4 ${isEditing ? 'border-black/10 bg-black/[0.02]' : 'border-amber-200 bg-amber-50/40'}`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="truncate text-sm font-semibold text-black">
                {server.name.trim() || '未命名 MCP'}
              </span>
              <span className="rounded-full bg-black/5 px-2 py-0.5 text-[11px] text-black/55">HTTP</span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] ${isEditing ? 'bg-black/5 text-black/55' : 'bg-amber-100 text-amber-700'}`}>
                {isEditing ? '编辑中' : '待设置'}
              </span>
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              onClick={() => void (isEditing ? saveEditingMcpServer(server) : savePendingMcpServer(server))}
              disabled={saving}
              className="h-9 rounded-full bg-black px-3 text-xs font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {saving ? '保存中' : '保存'}
            </button>
            {isEditing && (
              <button
                type="button"
                onClick={() => cancelEditingMcpServer(server.id)}
                disabled={saving}
                className="h-9 rounded-full border border-black/10 bg-white/70 px-3 text-xs font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
              >
                取消
              </button>
            )}
            <button
              type="button"
              onClick={() => void (isEditing ? removeMcpServer(server.id) : removePendingMcpServer(server.id))}
              disabled={saving}
              className="h-9 rounded-full border border-red-200 bg-white/70 px-3 text-xs font-medium text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              删除
            </button>
          </div>
        </div>
        <div className="mt-4 grid gap-3">
          <label className="grid gap-1.5 text-xs font-medium text-black/60">
            名称
            <input
              value={server.name}
              onChange={(event) => patchMcpFormServer(mode, server.id, { name: event.target.value })}
              className="h-10 rounded-xl border border-black/10 bg-white px-3 text-sm text-black outline-none transition focus:border-black/30"
              placeholder="MCP server name"
            />
          </label>
          <label className="grid gap-1.5 text-xs font-medium text-black/60">
            HTTP URL
            <input
              value={server.url}
              onChange={(event) => patchMcpFormServer(mode, server.id, { url: event.target.value })}
              className="h-10 rounded-xl border border-black/10 bg-white px-3 font-mono text-xs text-black outline-none transition focus:border-black/30"
              placeholder="https://example.com/mcp"
            />
          </label>
          <label className="inline-flex w-fit items-center gap-2 rounded-full border border-black/10 bg-white px-3 py-2 text-xs font-medium text-black">
            <input
              type="checkbox"
              checked={server.planning_enabled}
              onChange={(event) => patchMcpFormServer(mode, server.id, { planning_enabled: event.target.checked })}
              className="h-3.5 w-3.5 accent-black"
            />
            规划阶段可用
          </label>
          <div className="grid gap-1.5 text-xs font-medium text-black/60">
            Headers
            <div className="grid gap-2">
              {server.headers.map((header, index) => (
                <div key={index} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <input
                    value={header.name}
                    onChange={(event) => updateMcpFormHeader(mode, server.id, index, { name: event.target.value })}
                    className="h-10 rounded-xl border border-black/10 bg-white px-3 font-mono text-xs text-black outline-none transition focus:border-black/30"
                    placeholder="Header-Name"
                  />
                  <input
                    value={header.value}
                    onChange={(event) => updateMcpFormHeader(mode, server.id, index, { value: event.target.value })}
                    className="h-10 rounded-xl border border-black/10 bg-white px-3 font-mono text-xs text-black outline-none transition focus:border-black/30"
                    placeholder="value"
                  />
                  <button
                    type="button"
                    onClick={() => patchMcpFormServer(mode, server.id, {
                      headers: server.headers.filter((_, itemIndex) => itemIndex !== index),
                    })}
                    className="h-10 rounded-full px-3 text-xs text-red-600 hover:bg-red-50"
                  >
                    删除
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={() => patchMcpFormServer(mode, server.id, {
                  headers: [...server.headers, { name: '', value: '' }],
                })}
                className="h-9 rounded-full border border-black/10 bg-white px-3 text-xs font-medium text-black hover:bg-black/5"
              >
                添加 Header
              </button>
            </div>
          </div>
          <div className="grid gap-1.5 text-xs font-medium text-black/60">
            环境变量名
            <div className="grid gap-2">
              {server.env_var_names.map((name, index) => (
                <div key={index} className="flex gap-2">
                  <input
                    value={name}
                    onChange={(event) => updateMcpFormEnvVarName(mode, server.id, index, event.target.value)}
                    className="h-10 min-w-0 flex-1 rounded-xl border border-black/10 bg-white px-3 font-mono text-xs text-black outline-none transition focus:border-black/30"
                    placeholder="ENV_NAME"
                  />
                  <button
                    type="button"
                    onClick={() => patchMcpFormServer(mode, server.id, {
                      env_var_names: server.env_var_names.filter((_, itemIndex) => itemIndex !== index),
                    })}
                    className="h-10 rounded-full px-3 text-xs text-red-600 hover:bg-red-50"
                  >
                    删除
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={() => patchMcpFormServer(mode, server.id, {
                  env_var_names: [...server.env_var_names, ''],
                })}
                className="h-9 rounded-full border border-black/10 bg-white px-3 text-xs font-medium text-black hover:bg-black/5"
              >
                添加变量
              </button>
            </div>
          </div>
        </div>
      </section>
    );
  }

  function renderMcpSettings() {
    if (!draft) return null;
    return (
      <div className="space-y-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wider text-black/45">Global MCP</div>
            <h4 className="mt-1 text-xl font-semibold tracking-tight text-black">HTTP MCP Server</h4>
          </div>
          <button
            type="button"
            onClick={addPendingMcpServer}
            className="inline-flex h-10 items-center gap-2 rounded-full bg-black px-4 text-sm font-medium text-white transition hover:bg-black/85"
          >
            <PlusIcon className="h-4 w-4" />
            添加 MCP
          </button>
        </div>
        <div className="grid gap-3">
          {pendingMcpServers.map((server) => renderMcpFormCard(server, 'pending'))}
          {draft.mcp_servers.map((server) => {
            const editingServer = editingMcpServers[server.id];
            if (editingServer) return renderMcpFormCard(editingServer, 'editing');
            return (
              <section key={server.id} className="rounded-2xl border border-black/10 bg-white p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold text-black">{server.name}</span>
                      <span className="rounded-full bg-black/5 px-2 py-0.5 text-[11px] text-black/55">HTTP</span>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] ${server.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-black/5 text-black/50'}`}>
                        {server.enabled ? '已启用' : '已禁用'}
                      </span>
                      {server.planning_enabled && (
                        <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[11px] text-sky-700">规划可用</span>
                      )}
                    </div>
                    <code className="mt-2 block truncate rounded-xl border border-black/10 bg-black/[0.02] px-3 py-2 font-mono text-[11px] text-black/60">{server.url}</code>
                    <div className="mt-2 flex flex-wrap gap-2 text-[11px] text-black/55">
                      {server.headers.length > 0 && <span className="rounded-full border border-black/10 bg-black/[0.02] px-2 py-1">{server.headers.length} Headers</span>}
                      {server.env_var_names.length > 0 && <span className="rounded-full border border-black/10 bg-black/[0.02] px-2 py-1">{server.env_var_names.length} 环境变量</span>}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <button
                      type="button"
                      onClick={() => startEditingMcpServer(server)}
                      disabled={saving}
                      className="h-9 rounded-full border border-black/10 px-3 text-xs font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      编辑
                    </button>
                    <button
                      type="button"
                      onClick={() => void toggleMcpServerEnabled(server)}
                      disabled={saving}
                      className="h-9 rounded-full border border-black/10 px-3 text-xs font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {server.enabled ? '禁用' : '启用'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void toggleMcpServerPlanningEnabled(server)}
                      disabled={saving}
                      className="h-9 rounded-full border border-black/10 px-3 text-xs font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {server.planning_enabled ? '取消规划可用' : '设为规划可用'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void removeMcpServer(server.id)}
                      disabled={saving}
                      className="h-9 rounded-full border border-red-200 px-3 text-xs font-medium text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      删除
                    </button>
                  </div>
                </div>
              </section>
            );
          })}
        </div>
        {draft.mcp_servers.length === 0 && pendingMcpServers.length === 0 && (
          <div className="rounded-2xl border border-dashed border-black/15 bg-black/[0.02] px-3 py-10 text-center text-sm text-black/45">
            暂无 HTTP MCP Server
          </div>
        )}
      </div>
    );
  }

  function renderContent() {
    if (loading && !draft) return <div className="py-16 text-center text-sm text-black/45">加载设置中…</div>;
    if (!draft) return <div className="py-16 text-center text-sm text-black/45">暂无设置</div>;
    if (activeCategory === 'codex') return renderCodexSettings();
    if (activeCategory === 'instructions') return renderInstructionSettings();
    if (activeCategory === 'prompts') return renderPromptSettings();
    if (activeCategory === 'skills') return renderSkillsSettings();
    return renderMcpSettings();
  }

  const editingPromptVariables = editingPrompt?.variables.length
    ? editingPrompt.variables.map((item) => `$${item}`).join(' / ')
    : '无变量';
  const savingEditingPrompt = editingPrompt ? savingPromptKey === editingPrompt.key : false;

  return (
    <>
      <AppDialog
        open={open}
        onClose={saving || isSavingInstructions || savingPromptKey !== null ? () => undefined : onClose}
        title="设置"
        widthClassName="max-w-5xl"
      >
        <div className="flex h-[680px] max-h-[calc(100vh-220px)] min-h-[420px] overflow-hidden rounded-2xl border border-black/10">
          <nav className="w-60 shrink-0 border-r border-black/10 bg-black/[0.02] p-3" aria-label="设置分类">
            <div className="grid gap-2">
              {categories.map((category) => {
                const active = category.id === activeCategory;
                return (
                  <button
                    key={category.id}
                    type="button"
                    onClick={() => {
                      setActiveCategory(category.id);
                      setError('');
                    }}
                    className={`rounded-2xl border px-3 py-3 text-left transition ${active ? 'border-black bg-white shadow-card' : 'border-transparent hover:border-black/10 hover:bg-white'}`}
                  >
                    <span className="block text-sm font-semibold text-black">{category.label}</span>
                    <span className="mt-0.5 block text-xs text-black/50">{category.description}</span>
                  </button>
                );
              })}
            </div>
          </nav>
          <div className={`min-w-0 flex-1 overflow-y-auto p-5 ${SETTINGS_SCROLLBAR_CLASSES}`}>
            {error && (
              <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-5 text-amber-800">
                {error}
              </div>
            )}
            {renderContent()}
          </div>
        </div>
      </AppDialog>

      <AppDialog
        open={!!editingPrompt}
        onClose={savingPromptKey !== null ? () => undefined : closePromptEditor}
        title={editingPrompt ? `编辑提示词 · ${editingPrompt.name}` : '编辑提示词'}
        description={editingPrompt?.key}
        widthClassName="max-w-4xl"
        footer={
          <>
            <button
              type="button"
              onClick={closePromptEditor}
              disabled={savingPromptKey !== null}
              className="h-10 rounded-full border border-black/10 px-4 text-sm font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => {
                if (editingPrompt) void handleSavePrompt(editingPrompt, editingPromptDraft);
              }}
              disabled={!editingPrompt || savingPromptKey !== null || isLoadingPrompts}
              className="h-10 rounded-full bg-black px-4 text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {savingEditingPrompt ? '保存中' : '保存并同步'}
            </button>
          </>
        }
      >
        {editingPrompt ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2 text-[11px] text-black/55">
              <span className="rounded-full border border-black/10 bg-black/[0.02] px-2 py-1 font-mono">{editingPromptVariables}</span>
              <span className="rounded-full border border-black/10 bg-black/[0.02] px-2 py-1">
                {formatDate(editingPrompt.updated_at)}
              </span>
            </div>
            {editingPrompt.description && (
              <p className="text-xs leading-5 text-black/55">{editingPrompt.description}</p>
            )}
            <textarea
              value={editingPromptDraft}
              onChange={(event) => setEditingPromptDraft(event.target.value)}
              disabled={savingPromptKey !== null}
              spellCheck={false}
              className={`h-[460px] max-h-[calc(100vh-360px)] min-h-[260px] w-full resize-y rounded-xl border border-black/10 bg-[#101113] px-3 py-3 font-mono text-xs leading-5 text-white outline-none transition placeholder:text-white/35 focus:border-black/30 disabled:cursor-wait disabled:opacity-60 ${SETTINGS_DARK_SCROLLBAR_CLASSES}`}
              placeholder="Prompt content"
            />
          </div>
        ) : null}
      </AppDialog>

      <AppDialog
        open={!!previewingSkill}
        onClose={closeSkillPreview}
        title={previewingSkill ? `${previewingSkill.name} · SKILL.md` : 'SKILL.md'}
        description={skillMarkdown?.path ? skillMarkdown.path : undefined}
        widthClassName="max-w-3xl"
      >
        <div className={`h-[520px] max-h-[calc(100vh-260px)] overflow-y-auto rounded-2xl border border-black/10 bg-white px-5 py-4 ${SETTINGS_SCROLLBAR_CLASSES}`}>
          {isLoadingSkillMarkdown ? (
            <div className="py-16 text-center text-sm text-black/45">正在加载 SKILL.md...</div>
          ) : skillMarkdownError ? (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-3 py-2 text-sm leading-5 text-red-700">
              {skillMarkdownError}
            </div>
          ) : skillMarkdown?.content.trim() ? (
            <div className="prose prose-sm max-w-none text-sm leading-6 text-black/75 [&_h1]:mt-0 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mt-5 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mt-4 [&_h3]:text-sm [&_h3]:font-semibold [&_p]:my-2 [&_ul]:my-2 [&_ol]:my-2 [&_li]:my-0.5 [&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-black/15 [&_blockquote]:pl-3 [&_blockquote]:text-black/60 [&_code]:rounded [&_code]:bg-black/[0.06] [&_code]:px-1 [&_code]:py-[1px] [&_code]:text-[12px] [&_pre]:my-2 [&_pre]:max-w-full [&_pre]:overflow-x-auto [&_pre]:whitespace-pre-wrap [&_pre]:break-words [&_pre]:rounded-xl [&_pre]:bg-black/[0.04] [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-black/10 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_td]:border [&_td]:border-black/10 [&_td]:px-2 [&_td]:py-1 [&_hr]:my-3 [&_hr]:border-black/10">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{skillMarkdown.content}</ReactMarkdown>
            </div>
          ) : (
            <div className="py-16 text-center text-sm text-black/45">SKILL.md 内容为空。</div>
          )}
        </div>
      </AppDialog>
    </>
  );
}
