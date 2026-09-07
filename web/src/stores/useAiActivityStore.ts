import { create } from 'zustand';
import * as api from '../lib/api';
import type { AiActivity } from '../types';

interface AiActivityState {
  activities: AiActivity[];
  loading: boolean;
  refresh(): Promise<void>;
  stop(activity: AiActivity): Promise<void>;
}

let refreshPromise: Promise<void> | null = null;

export const useAiActivityStore = create<AiActivityState>((set) => ({
  activities: [],
  loading: false,

  async refresh() {
    if (refreshPromise) return refreshPromise;
    set({ loading: true });
    refreshPromise = api.listAiActivities()
      .then((activities) => set({ activities }))
      .finally(() => {
        refreshPromise = null;
        set({ loading: false });
      });
    return refreshPromise;
  },

  async stop(activity) {
    if (!activity.cancellable) return;
    if (activity.kind === 'app_run') await api.cancelRun(activity.id);
    else if (activity.kind === 'workspace_turn') await api.interruptWorkspaceTurn(activity.id);
    else if (activity.kind === 'nl_compile') await api.cancelNlCompile(activity.id);
    else if (activity.kind === 'prompt_assistant') await api.cancelPromptAssistant(activity.id);
    else await api.cancelWikiOperation(activity.id);
    set((state) => ({ activities: state.activities.filter((item) => item.id !== activity.id) }));
  },
}));
