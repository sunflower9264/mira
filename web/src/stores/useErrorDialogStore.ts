import { create } from 'zustand';

interface ErrorDialogPayload {
  title?: string;
  message: string;
}

interface ErrorDialogState {
  dialog: ErrorDialogPayload | null;
  show(payload: ErrorDialogPayload): void;
  close(): void;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export const useErrorDialogStore = create<ErrorDialogState>((set) => ({
  dialog: null,
  show(payload) {
    set({ dialog: payload });
  },
  close() {
    set({ dialog: null });
  },
}));

export function showErrorDialog(message: string, title = '操作失败'): void {
  useErrorDialogStore.getState().show({ title, message });
}

export function showCaughtError(error: unknown, fallback = '操作失败，请稍后重试', title = '操作失败'): void {
  showErrorDialog(errorMessage(error, fallback), title);
}
