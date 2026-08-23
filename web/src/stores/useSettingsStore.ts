import { create } from 'zustand';
import type {
  CodexConfigFile,
  CodexSetupState,
  CodexStatus,
  InstructionFile,
  McpServerConfig,
  MiraSettings,
  PromptTemplate,
  SkillConfig,
} from '../types';
import * as api from '../lib/api';

interface SettingsStoreState {
  settings: MiraSettings | null;
  codexSetupState: CodexSetupState | null;
  loading: boolean;
  loadingCodexSetupState: boolean;
  saving: boolean;
  error: string | null;
  reset(): void;
  load(): Promise<MiraSettings>;
  loadCodexSetupState(force?: boolean): Promise<CodexSetupState>;
  updateSkillEnabled(skillId: string, enabled: boolean): Promise<MiraSettings>;
  updateSkillPlanningEnabled(skillId: string, planningEnabled: boolean): Promise<MiraSettings>;
  deleteSkill(skillId: string): Promise<void>;
  addMcpServer(server: McpServerConfig): Promise<MiraSettings>;
  updateMcpServer(serverId: string, server: McpServerConfig): Promise<MiraSettings>;
  deleteMcpServer(serverId: string): Promise<void>;
  loadCodexConfig(): Promise<CodexConfigFile>;
  saveCodexConfig(
    content: string,
    options: { authContent: string; supportedModels: string[] },
  ): Promise<CodexConfigFile>;
  loadInstructionFile(): Promise<InstructionFile>;
  saveInstructionFile(content: string): Promise<InstructionFile>;
  loadPromptTemplates(): Promise<PromptTemplate[]>;
  savePromptTemplate(key: string, content: string): Promise<PromptTemplate>;
  parseSkillArchive(archive: File): Promise<SkillConfig>;
  refreshCodexStatus(): Promise<CodexStatus>;
}

export const useSettingsStore = create<SettingsStoreState>((set, get) => ({
  settings: null,
  codexSetupState: null,
  loading: false,
  loadingCodexSetupState: false,
  saving: false,
  error: null,
  reset() {
    set({
      settings: null,
      codexSetupState: null,
      loading: false,
      loadingCodexSetupState: false,
      saving: false,
      error: null,
    });
  },
  async load() {
    const current = get().settings;
    if (current) return current;
    set({ loading: true, error: null });
    try {
      const settings = await api.getSettings();
      set({ settings, loading: false });
      return settings;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      throw e;
    }
  },
  async loadCodexSetupState(force = false) {
    const current = get().codexSetupState;
    if (current && !force) return current;
    set({ loadingCodexSetupState: true, error: null });
    try {
      const codexSetupState = await api.getCodexSetupState();
      set({ codexSetupState, loadingCodexSetupState: false });
      return codexSetupState;
    } catch (e) {
      set({ error: (e as Error).message, loadingCodexSetupState: false });
      throw e;
    }
  },
  async updateSkillEnabled(skillId, enabled) {
    set({ saving: true, error: null });
    try {
      const saved = await api.updateSkillEnabled(skillId, enabled);
      set({ settings: saved, saving: false });
      return saved;
    } catch (e) {
      set({ error: (e as Error).message, saving: false });
      throw e;
    }
  },
  async updateSkillPlanningEnabled(skillId, planningEnabled) {
    set({ saving: true, error: null });
    try {
      const saved = await api.updateSkillPlanningEnabled(skillId, planningEnabled);
      set({ settings: saved, saving: false });
      return saved;
    } catch (e) {
      set({ error: (e as Error).message, saving: false });
      throw e;
    }
  },
  async deleteSkill(skillId) {
    set({ saving: true, error: null });
    try {
      await api.deleteSkill(skillId);
      set((state) => state.settings ? {
        settings: {
          ...state.settings,
          skills: state.settings.skills.filter((skill) => skill.id !== skillId),
          tools: state.settings.tools.filter((tool) => tool.id !== `skill:${skillId}`),
        },
        saving: false,
      } : { saving: false });
    } catch (e) {
      set({ error: (e as Error).message, saving: false });
      throw e;
    }
  },
  async addMcpServer(server) {
    set({ saving: true, error: null });
    try {
      const saved = await api.addMcpServer(server);
      set({ settings: saved, saving: false });
      return saved;
    } catch (e) {
      set({ error: (e as Error).message, saving: false });
      throw e;
    }
  },
  async updateMcpServer(serverId, server) {
    set({ saving: true, error: null });
    try {
      const saved = await api.updateMcpServer(serverId, server);
      set({ settings: saved, saving: false });
      return saved;
    } catch (e) {
      set({ error: (e as Error).message, saving: false });
      throw e;
    }
  },
  async deleteMcpServer(serverId) {
    set({ saving: true, error: null });
    try {
      await api.deleteMcpServer(serverId);
      set((state) => state.settings ? {
        settings: {
          ...state.settings,
          mcp_servers: state.settings.mcp_servers.filter((server) => server.id !== serverId),
          tools: state.settings.tools.filter((tool) => tool.id !== `mcp:${serverId}`),
        },
        saving: false,
      } : { saving: false });
    } catch (e) {
      set({ error: (e as Error).message, saving: false });
      throw e;
    }
  },
  async loadCodexConfig() {
    return api.getCodexConfig();
  },
  async saveCodexConfig(content, options) {
    const saved = await api.saveCodexConfig(content, options);
    if (saved.settings) set({ settings: saved.settings });
    return saved;
  },
  async loadInstructionFile() {
    return api.getInstructionFile();
  },
  async saveInstructionFile(content) {
    return api.saveInstructionFile(content);
  },
  async loadPromptTemplates() {
    return api.getPromptTemplates();
  },
  async savePromptTemplate(key, content) {
    return api.savePromptTemplate(key, content);
  },
  async parseSkillArchive(archive) {
    const skill = await api.parseSkillArchive(archive);
      set((state) => state.settings ? {
        settings: {
          ...state.settings,
          skills: [skill, ...state.settings.skills],
          tools: [
            {
              id: `skill:${skill.id}`,
              name: skill.name,
              description: skill.description,
              enabled: skill.enabled,
              planning_enabled: skill.planning_enabled,
            },
            ...state.settings.tools,
          ],
        },
      } : {});
    return skill;
  },
  async refreshCodexStatus() {
    return api.refreshCodexStatus();
  },
}));
