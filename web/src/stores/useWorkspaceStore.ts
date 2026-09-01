import { create } from 'zustand';
import * as api from '../lib/api';
import type { Workspace, WorkspaceCreateInput } from '../types';

interface WorkspaceStoreState {
  workspaces: Workspace[];
  loading: boolean;
  error: string | null;
  load(force?: boolean): Promise<void>;
  create(input: WorkspaceCreateInput): Promise<Workspace>;
  update(id: string, input: { name?: string; description?: string }): Promise<Workspace>;
  remove(id: string): Promise<void>;
}

let loaded = false;

export const useWorkspaceStore = create<WorkspaceStoreState>((set, get) => ({
  workspaces: [],
  loading: false,
  error: null,

  async load(force = false) {
    if (get().loading || (loaded && !force)) return;
    set({ loading: true, error: null });
    try {
      const workspaces = await api.listWorkspaces();
      loaded = true;
      set({ workspaces, loading: false });
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : '加载工作空间失败' });
    }
  },

  async create(input) {
    const workspace = await api.createWorkspace(input);
    set((state) => ({ workspaces: [workspace, ...state.workspaces] }));
    return workspace;
  },

  async update(id, input) {
    const workspace = await api.updateWorkspace(id, input);
    set((state) => ({
      workspaces: state.workspaces.map((item) => (item.id === id ? workspace : item)),
    }));
    return workspace;
  },

  async remove(id) {
    await api.deleteWorkspace(id);
    set((state) => ({ workspaces: state.workspaces.filter((item) => item.id !== id) }));
  },
}));
