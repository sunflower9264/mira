import { AppDialog } from './AppDialog';

interface ConfirmDialogProps {
  open: boolean;
  onClose(): void;
  onConfirm(): void | Promise<void>;
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: 'default' | 'danger';
  busy?: boolean;
  confirmDisabled?: boolean;
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = '确认',
  cancelLabel = '取消',
  tone = 'default',
  busy = false,
  confirmDisabled = false,
}: ConfirmDialogProps) {
  return (
    <AppDialog
      open={open}
      onClose={busy ? () => undefined : onClose}
      title={title}
      description={description}
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-full border border-black/10 px-4 py-2 text-sm font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={() => void onConfirm()}
            disabled={busy || confirmDisabled}
            className={`rounded-full px-4 py-2 text-sm font-medium text-white transition disabled:cursor-not-allowed disabled:opacity-50 ${
              tone === 'danger'
                ? 'bg-red-600 hover:bg-red-500'
                : 'bg-black hover:bg-black/85'
            }`}
          >
            {confirmLabel}
          </button>
        </>
      }
    />
  );
}
