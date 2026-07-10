// Owns "my apps", template apps, market, and recent run lists for the Home page.

import { create } from 'zustand';
import type { App } from '../types';
import * as api from '../lib/api';

interface AppStoreState {
  myApps: App[];
  templates: App[];
  market: App[];
  recentRuns: App[];
  loading: boolean;
  error: string | null;
  load(): Promise<void>;
  createBlank(): Promise<App>;
  cloneFromMarket(appId: string): Promise<App>;
  cloneTemplate(templateId: string): Promise<App>;
  rename(id: string, name: string): Promise<void>;
  remove(id: string): Promise<void>;
}

export const useAppStore = create<AppStoreState>((set, get) => ({
  myApps: [],
  templates: [],
  market: [],
  recentRuns: [],
  loading: false,
  error: null,
  async load() {
    set({ loading: true, error: null });
    try {
      const [myApps, templates, market, recentRuns] = await Promise.all([
        api.listMyApps(),
        api.listTemplates(),
        api.listMarket(),
        api.listRecentRuns(),
      ]);
      set({ myApps, templates, market, recentRuns, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },
  async createBlank() {
    const app = await api.createApp({});
    set({ myApps: [app, ...get().myApps] });
    return app;
  },
  async cloneFromMarket(appId) {
    const app = await api.cloneApp(appId);
    set({ myApps: [app, ...get().myApps.filter((a) => a.id !== app.id)] });
    return app;
  },
  async cloneTemplate(templateId) {
    const app = await api.cloneFromGallery(templateId);
    set({ myApps: [app, ...get().myApps.filter((a) => a.id !== app.id)] });
    return app;
  },
  async rename(id, name) {
    const updated = await api.patchApp(id, { name });
    set({ myApps: get().myApps.map((a) => (a.id === id ? updated : a)) });
  },
  async remove(id) {
    await api.deleteApp(id);
    set({ myApps: get().myApps.filter((a) => a.id !== id) });
  },
}));
