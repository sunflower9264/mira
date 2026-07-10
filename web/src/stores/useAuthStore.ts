// 鉴权 store：基于 lib/api 的真实 HTTP 接口，token 与 user 持久化在 lib/auth。
// 启动时 bootstrap() 会用本地 token 调用 /api/auth/me 验证；401 时自动清空。

import { create } from 'zustand';
import {
  type AuthUser,
  clearToken,
  clearUser,
  getToken,
  getUser,
  setToken,
  setUser,
} from '../lib/auth';
import * as api from '../lib/api';
import { useSettingsStore } from './useSettingsStore';

interface AuthStoreState {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  login(username: string, password: string): Promise<void>;
  logout(): void;
  bootstrap(): Promise<void>;
}

/** 选择器：当前是否管理员（用于设置入口显隐）。 */
export function selectIsAdmin(state: AuthStoreState): boolean {
  return state.user?.is_admin === true;
}

function applySession(set: (p: Partial<AuthStoreState>) => void, session: api.AuthSession) {
  useSettingsStore.getState().reset();
  setToken(session.token);
  setUser(session.user);
  set({ user: session.user, error: null });
}

function clearSession(set: (p: Partial<AuthStoreState>) => void) {
  useSettingsStore.getState().reset();
  clearToken();
  clearUser();
  set({ user: null });
}

export const useAuthStore = create<AuthStoreState>((set) => ({
  user: getUser(),
  loading: false,
  error: null,

  async login(username, password) {
    set({ loading: true, error: null });
    try {
      const session = await api.login({ username, password });
      applySession(set, session);
    } catch (e) {
      set({ error: e instanceof Error ? e.message : '登录失败' });
      throw e;
    } finally {
      set({ loading: false });
    }
  },

  logout() {
    clearSession(set);
  },

  async bootstrap() {
    if (!getToken()) return;
    try {
      const user = await api.me();
      setUser(user);
      set({ user });
    } catch {
      // token 失效或网络错误：保守起见清空登录态。
      clearSession(set);
    }
  },
}));
