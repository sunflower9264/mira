import { useEffect, useState } from 'react';
import type { App } from '../types';
import { fetchAppCoverBlob } from '../lib/api';

export function isAppCoverUploadId(cover: string | null | undefined): cover is string {
  return Boolean(cover?.startsWith('upl_') && !cover.includes('/') && !cover.includes('\\'));
}

export function useAppCoverUrl(app: Pick<App, 'id' | 'cover'> | null): string | null {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!app || !isAppCoverUploadId(app.cover)) {
      setUrl(null);
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    let objectUrl: string | null = null;
    setUrl(null);
    fetchAppCoverBlob(app.id, controller.signal)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setUrl(null);
      });
    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [app?.id, app?.cover]);

  return url;
}
