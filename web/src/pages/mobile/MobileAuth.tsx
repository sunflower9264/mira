import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useAuthStore } from '../../stores/useAuthStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';

export function MobileAuth() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const welcomeText = useTypedText('欢迎来到Mira');

  const trimmedUsername = username.trim();
  const disabled = useMemo(
    () => busy || trimmedUsername.length === 0 || password.length === 0,
    [busy, trimmedUsername, password],
  );

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (disabled) return;
    setBusy(true);
    try {
      await login(trimmedUsername, password);
      navigate('/m');
    } catch (err) {
      showCaughtError(err, '登录失败', '登录失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-dvh bg-[#F4F5F7] text-[#0B0B0F]">
      <main className="flex min-h-dvh items-center justify-center px-5 pb-[calc(env(safe-area-inset-bottom)+32px)] pt-[calc(env(safe-area-inset-top)+32px)]">
        <div className="w-full max-w-sm">
          <h1 className="mb-8 min-h-[2.5rem] text-center text-3xl font-semibold tracking-tight" aria-label="欢迎来到Mira">
            {welcomeText}
            {welcomeText.length < '欢迎来到Mira'.length ? (
              <span className="ml-0.5 inline-block h-8 w-[2px] translate-y-1 bg-black/75" aria-hidden="true" />
            ) : null}
          </h1>

          <form onSubmit={handleSubmit} className="rounded-[24px] border border-black/5 bg-white p-5 shadow-card" noValidate>
            <MobileField
              label="用户名"
              value={username}
              onChange={setUsername}
              autoComplete="username"
              placeholder="your-name"
            />
            <MobileField
              label="密码"
              type="password"
              value={password}
              onChange={setPassword}
              autoComplete="current-password"
              placeholder="••••••••"
            />

            <button
              type="submit"
              disabled={disabled}
              className="mt-5 flex h-12 w-full items-center justify-center gap-2 rounded-full bg-black px-4 text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-45"
            >
              {busy ? '登录中...' : '登录'}
              {!busy ? <ArrowRight className="h-4 w-4" /> : null}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}

function useTypedText(text: string) {
  const [value, setValue] = useState('');

  useEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduceMotion) {
      setValue(text);
      return;
    }

    let index = 0;
    setValue('');
    const timer = window.setInterval(() => {
      index += 1;
      setValue(text.slice(0, index));
      if (index >= text.length) window.clearInterval(timer);
    }, 90);

    return () => window.clearInterval(timer);
  }, [text]);

  return value;
}

function MobileField({
  label,
  value,
  onChange,
  type = 'text',
  autoComplete,
  placeholder,
}: {
  label: string;
  value: string;
  onChange(value: string): void;
  type?: string;
  autoComplete?: string;
  placeholder?: string;
}) {
  return (
    <label className="mt-4 block first:mt-0">
      <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-black/45">{label}</span>
      <input
        type={type}
        value={value}
        autoComplete={autoComplete}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 h-11 w-full rounded-2xl border border-black/10 bg-white px-3 text-base outline-none transition focus:border-black/35"
      />
    </label>
  );
}
