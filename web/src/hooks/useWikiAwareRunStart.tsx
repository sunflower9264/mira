import { useCallback, useState } from 'react';
import * as api from '../lib/api';
import { useRunStore } from '../stores/useRunStore';
import type { App, WikiAccess } from '../types';
import { WikiAccessDialog } from '../components/common/WikiAccessDialog';

export function useWikiAwareRunStart(app: App | null, onStarted?: () => void) {
  const startRun = useRunStore((state) => state.start);
  const [access, setAccess] = useState<WikiAccess | null>(null);
  const [pendingInputs, setPendingInputs] = useState<Record<string, unknown> | null>(null);

  const start = useCallback(async (inputs: Record<string, unknown>) => {
    if (!app) throw new Error('应用不存在');
    const status = await api.getWikiAccess(app.id);
    if (status.requires_consent) {
      setPendingInputs(inputs);
      setAccess(status);
      return false;
    }
    await startRun(app, inputs, 'auto');
    onStarted?.();
    return true;
  }, [app, onStarted, startRun]);

  const close = useCallback(() => {
    setAccess(null);
    setPendingInputs(null);
  }, []);

  const allow = useCallback(async () => {
    if (!app || !access || !pendingInputs) return;
    await api.grantWikiAccess(app.id, access.graph_sha256);
    await startRun(app, pendingInputs, 'auto');
    close();
    onStarted?.();
  }, [access, app, close, onStarted, pendingInputs, startRun]);

  const skip = useCallback(async () => {
    if (!app || !pendingInputs) return;
    await startRun(app, pendingInputs, 'without');
    close();
    onStarted?.();
  }, [app, close, onStarted, pendingInputs, startRun]);

  const dialog = (
    <WikiAccessDialog
      access={access}
      appName={app?.name ?? ''}
      onClose={close}
      onAllow={allow}
      onSkip={skip}
    />
  );

  return { start, dialog };
}
