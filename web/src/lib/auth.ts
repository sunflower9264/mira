// 鉴权本地持久化：仅保存 Bearer Token 与用户摘要。
// Token 与用户摘要分开存储，方便 401 时只清 token 不破坏 UI 上的可视字段。

const TOKEN_KEY = 'mira-auth-token-v1';
const USER_KEY = 'mira-auth-user-v1';

export interface AuthUser {
  username: string;
  is_admin: boolean;
}

export function getToken(): string | null {
  if (typeof localStorage === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.removeItem(TOKEN_KEY);
}

export function getUser(): AuthUser | null {
  if (typeof localStorage === 'undefined') return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<AuthUser> & { username?: string };
    if (!parsed || typeof parsed.username !== 'string' || parsed.username.length === 0) {
      return null;
    }
    return { username: parsed.username, is_admin: parsed.is_admin === true };
  } catch {
    return null;
  }
}

export function setUser(user: AuthUser): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearUser(): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.removeItem(USER_KEY);
}
