import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '../stores/useAuthStore';
import { showCaughtError } from '../stores/useErrorDialogStore';

export function Login() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const trimmedUsername = username.trim();
  const disabled = busy || trimmedUsername.length === 0 || password.length === 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (disabled) return;
    setBusy(true);
    try {
      await login(trimmedUsername, password);
      navigate('/');
    } catch (err) {
      showCaughtError(err, '登录失败', '登录失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-full flex flex-col">
      <header className="h-16 px-6 flex items-center border-b border-black/5 bg-white/60 backdrop-blur">
        <div className="flex items-center gap-3">
          <span className="text-xl font-semibold tracking-tight">Mira</span>
        </div>
      </header>
      <main className="flex-1 flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm rounded-[24px] bg-white p-7 shadow-[0_20px_70px_rgba(0,0,0,0.08)] ring-1 ring-black/5">
          <h1 className="text-xl font-semibold tracking-tight">登录 Mira</h1>
          <p className="mt-2 text-sm leading-6 text-black/65">
            使用用户名和密码继续。
          </p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit} noValidate>
            <label className="block">
              <span className="block text-[11px] uppercase tracking-wider text-black/55">
                用户名
              </span>
              <input
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="your-name"
                className="mt-2 w-full rounded-xl border border-black/10 px-3 py-2.5 text-sm outline-none transition focus:border-black/30"
              />
            </label>
            <label className="block">
              <span className="block text-[11px] uppercase tracking-wider text-black/55">
                密码
              </span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="mt-2 w-full rounded-xl border border-black/10 px-3 py-2.5 text-sm outline-none transition focus:border-black/30"
              />
            </label>

            <button
              type="submit"
              disabled={disabled}
              className="mt-2 w-full rounded-full bg-black px-4 py-2.5 text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? '登录中…' : '登录'}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}
