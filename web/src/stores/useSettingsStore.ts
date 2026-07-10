import { create } from 'zustand';
import type {
  AgentConfigFile,
  AgentConfigKind,
  AgentProviderId,
  AgentProviderStatus,
  AgentSetupState,
  InstructionFile,
  McpServerConfig,
  MiraSettings,
  PromptTemplate,
  SkillConfig,
} from '../types';
import * as api from '../lib/api';

interface SettingsStoreState {
  settings: MiraSettings | null;
  agentSetupState: AgentSetupState | null;
  loading: boolean;
  loadingAgentSetupState: boolean;
  saving: boolean;
  error: string | null;
  reset(): void;
  load(): Promise<MiraSettings>;
  loadAgentSetupState(force?: boolean): Promise<AgentSetupState>;
  updateSkillEnabled(skillId: string, enabled: boolean): Promise<MiraSettings>;
  updateSkillPlanningEnabled(skillId: string, planningEnabled: boolean): Promise<MiraSettings>;
  deleteSkill(skillId: string): Promise<void>;
  addMcpServer(server: McpServerConfig): Promise<MiraSettings>;
  updateMcpServer(serverId: string, server: McpServerConfig): Promise<MiraSettings>;
  deleteMcpServer(serverId: string): Promise<void>;
  loadAgentConfig(agentId: AgentConfigKind): Promise<AgentConfigFile>;
  saveAgentConfig(
    agentId: Exclude<AgentConfigKind, 'codex-auth'>,
    content: string,
    options: { enabled?: boolean; authContent?: string; supportedModels: string[] },
  ): Promise<AgentConfigFile>;
  loadInstructionFile(provider: AgentProviderId): Promise<InstructionFile>;
  saveInstructionFile(provider: AgentProviderId, content: string): Promise<InstructionFile>;
  loadPromptTemplates(): Promise<PromptTemplate[]>;
  savePromptTemplate(key: string, content: string): Promise<PromptTemplate>;
  parseSkillArchive(archive: File): Promise<SkillConfig>;
  refreshAgentStatus(agentId: AgentProviderId): Promise<AgentProviderStatus>;
}

export const useSettingsStore = create<SettingsStoreState>((set, get) => ({
  settings: null,
  agentSetupState: null,
  loading: false,
  loadingAgentSetupState: false,
  saving: false,
  error: null,
  reset() {
    set({
      settings: null,
      agentSetupState: null,
      loading: false,
      loadingAgentSetupState: false,
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
  async loadAgentSetupState(force = false) {
    const current = get().agentSetupState;
    if (current && !force) return current;
    set({ loadingAgentSetupState: true, error: null });
    try {
      const agentSetupState = await api.getAgentSetupState();
      set({ agentSetupState, loadingAgentSetupState: false });
      return agentSetupState;
    } catch (e) {
      set({ error: (e as Error).message, loadingAgentSetupState: false });
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
  async loadAgentConfig(agentId) {
    return api.getAgentConfig(agentId);
  },
  async saveAgentConfig(agentId, content, options) {
    const saved = await api.saveAgentConfig(agentId, content, options);
    if (saved.settings) set({ settings: saved.settings });
    return saved;
  },
  async loadInstructionFile(provider) {
    return api.getInstructionFile(provider);
  },
  async saveInstructionFile(provider, content) {
    return api.saveInstructionFile(provider, content);
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
  async refreshAgentStatus(agentId) {
    const status = await api.refreshAgentStatus(agentId);
    set((state) => state.settings ? {
      settings: {
        ...state.settings,
        agents: state.settings.agents.map((agent) => agent.id === agentId ? { ...agent, status } : agent),
      },
    } : {});
    return status;
  },
}));
