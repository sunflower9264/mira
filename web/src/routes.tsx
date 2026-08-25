import { lazy, Suspense, useEffect, useState, type ReactElement } from 'react';
import { createBrowserRouter, Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from './stores/useAuthStore';
import { useSettingsStore } from './stores/useSettingsStore';
import { desktopPathFor, mobilePathFor, prefersMobileViewport } from './lib/mobile';

const Home = lazy(() => import('./pages/Home').then((m) => ({ default: m.Home })));
const Editor = lazy(() => import('./pages/Editor').then((m) => ({ default: m.Editor })));
const AppView = lazy(() => import('./pages/AppView').then((m) => ({ default: m.AppView })));
const Login = lazy(() => import('./pages/Login').then((m) => ({ default: m.Login })));
const Wiki = lazy(() => import('./pages/Wiki').then((m) => ({ default: m.Wiki })));
const MobileHome = lazy(() => import('./pages/mobile/MobileHome').then((m) => ({ default: m.MobileHome })));
const MobileAuth = lazy(() => import('./pages/mobile/MobileAuth').then((m) => ({ default: m.MobileAuth })));
const MobileRun = lazy(() => import('./pages/mobile/MobileRun').then((m) => ({ default: m.MobileRun })));
const AdminCodexOnboarding = lazy(() =>
  import('./components/settings/AdminCodexOnboarding').then((m) => ({ default: m.AdminCodexOnboarding })),
);

function PageLoading() {
  return (
    <div className="flex min-h-full items-center justify-center bg-[#f6f4ef] px-6 text-sm text-black/55">
      加载中...
    </div>
  );
}

function withSuspense(children: ReactElement) {
  return <Suspense fallback={<PageLoading />}>{children}</Suspense>;
}

function DeviceRouteGate({ children }: { children: ReactElement }) {
  const location = useLocation();
  const mobile = prefersMobileViewport();
  const target = mobile ? mobilePathFor(location.pathname) : desktopPathFor(location.pathname);
  if (target) return <Navigate to={`${target}${location.search}`} replace />;
  return children;
}

// 未登录时直接重定向到登录页，避免受保护页面 mount 后再发 401 请求触发兜底跳转。
function RequireAuth({ children, loginPath = '/login' }: { children: ReactElement; loginPath?: string }) {
  const user = useAuthStore((s) => s.user);
  if (!user) return <Navigate to={loginPath} replace />;
  return <AdminCodexSetupGate>{children}</AdminCodexSetupGate>;
}

function AdminCodexSetupGate({ children }: { children: ReactElement }) {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const codexSetupState = useSettingsStore((s) => s.codexSetupState);
  const loadingCodexSetupState = useSettingsStore((s) => s.loadingCodexSetupState);
  const loadCodexSetupState = useSettingsStore((s) => s.loadCodexSetupState);
  const [error, setError] = useState('');

  useEffect(() => {
    if (user?.is_admin !== true) return;
    setError('');
    void loadCodexSetupState(true).catch((e) => {
      setError(e instanceof Error ? e.message : '读取 Codex 初始化状态失败');
    });
  }, [loadCodexSetupState, user?.is_admin]);

  if (user?.is_admin !== true) return children;

  if (error) {
    return (
      <div className="flex min-h-full items-center justify-center bg-[#f6f4ef] px-6">
        <div className="w-full max-w-md rounded-2xl border border-amber-200 bg-white p-6 shadow-[0_16px_50px_rgba(0,0,0,0.06)]">
          <div className="text-sm font-semibold text-black">无法读取 Codex 初始化状态</div>
          <p className="mt-2 text-sm leading-6 text-black/60">{error}</p>
          <button
            type="button"
            onClick={logout}
            className="mt-5 h-10 rounded-full bg-black px-4 text-sm font-medium text-white transition hover:bg-black/85"
          >
            退出登录
          </button>
        </div>
      </div>
    );
  }

  if (loadingCodexSetupState || !codexSetupState) {
    return (
      <div className="flex min-h-full items-center justify-center bg-[#f6f4ef] px-6 text-sm text-black/55">
        正在检查 Codex 初始化状态...
      </div>
    );
  }

  if (!codexSetupState.completed) {
    return withSuspense(<AdminCodexOnboarding onCompleted={() => undefined} />);
  }

  return children;
}

export const router = createBrowserRouter([
  { path: '/', element: <DeviceRouteGate><RequireAuth>{withSuspense(<Home />)}</RequireAuth></DeviceRouteGate> },
  { path: '/login', element: <DeviceRouteGate>{withSuspense(<Login />)}</DeviceRouteGate> },
  { path: '/wiki', element: <RequireAuth>{withSuspense(<Wiki />)}</RequireAuth> },
  { path: '/apps/:id/editor', element: <DeviceRouteGate><RequireAuth>{withSuspense(<Editor />)}</RequireAuth></DeviceRouteGate> },
  { path: '/apps/:id/view', element: <DeviceRouteGate><RequireAuth>{withSuspense(<AppView />)}</RequireAuth></DeviceRouteGate> },
  { path: '/market/apps/:id', element: <DeviceRouteGate><RequireAuth>{withSuspense(<AppView readOnly />)}</RequireAuth></DeviceRouteGate> },
  { path: '/m', element: <DeviceRouteGate><RequireAuth loginPath="/m/login">{withSuspense(<MobileHome />)}</RequireAuth></DeviceRouteGate> },
  { path: '/m/login', element: <DeviceRouteGate>{withSuspense(<MobileAuth />)}</DeviceRouteGate> },
  { path: '/m/apps/:id/run', element: <DeviceRouteGate><RequireAuth loginPath="/m/login">{withSuspense(<MobileRun />)}</RequireAuth></DeviceRouteGate> },
  { path: '*', element: <Navigate to="/" replace /> },
]);
