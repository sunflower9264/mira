import { matchPath } from 'react-router-dom';

const MOBILE_MEDIA_QUERY = '(max-width: 767px)';

export function prefersMobileViewport(): boolean {
  if (typeof window === 'undefined') return false;
  return window.matchMedia(MOBILE_MEDIA_QUERY).matches;
}

export function installMobileZoomGuard(): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') return () => undefined;

  const mobileQuery = window.matchMedia(MOBILE_MEDIA_QUERY);
  let lastTouchEnd = 0;

  const shouldBlockZoom = () => mobileQuery.matches;

  const preventGestureZoom = (event: Event) => {
    if (shouldBlockZoom()) event.preventDefault();
  };

  const preventPinchZoom = (event: TouchEvent) => {
    if (shouldBlockZoom() && event.touches.length > 1) event.preventDefault();
  };

  const preventDoubleTapZoom = (event: TouchEvent) => {
    if (!shouldBlockZoom()) return;
    const now = Date.now();
    if (now - lastTouchEnd <= 300) event.preventDefault();
    lastTouchEnd = now;
  };

  document.addEventListener('touchmove', preventPinchZoom, { passive: false });
  document.addEventListener('touchend', preventDoubleTapZoom, { passive: false });
  document.addEventListener('gesturestart', preventGestureZoom, { passive: false });
  document.addEventListener('gesturechange', preventGestureZoom, { passive: false });
  document.addEventListener('gestureend', preventGestureZoom, { passive: false });

  return () => {
    document.removeEventListener('touchmove', preventPinchZoom);
    document.removeEventListener('touchend', preventDoubleTapZoom);
    document.removeEventListener('gesturestart', preventGestureZoom);
    document.removeEventListener('gesturechange', preventGestureZoom);
    document.removeEventListener('gestureend', preventGestureZoom);
  };
}

export function mobilePathFor(pathname: string): string | null {
  if (pathname.startsWith('/m')) return null;
  if (pathname === '/') return '/m';
  if (pathname === '/login') return '/m/login';
  const appMatch =
    matchPath('/apps/:id/editor', pathname) ??
    matchPath('/apps/:id/view', pathname) ??
    matchPath('/market/apps/:id', pathname);
  if (appMatch?.params.id) return `/m/apps/${appMatch.params.id}/run`;
  return null;
}

export function desktopPathFor(pathname: string): string | null {
  if (!pathname.startsWith('/m')) return null;
  if (pathname === '/m') return '/';
  if (pathname === '/m/login') return '/login';
  const appMatch = matchPath('/m/apps/:id/run', pathname);
  if (appMatch?.params.id) return `/apps/${appMatch.params.id}/view`;
  return null;
}
