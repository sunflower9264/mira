import { useEffect, useRef } from 'react';
import { AppDialog } from './AppDialog';

interface PromptDialogProps {
  open: boolean;
  onClose(): void;
  onConfirm(): void | Promise<void>;
  title: string;
  description?: React.ReactNode;
  value: string;
  onChange(value: string): void;
  confirmLabel?: string;
  cancelLabel?: string;
  placeholder?: string;
  inputLabel?: string;
  busy?: boolean;
  disabled?: boolean;
}

export function PromptDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  value,
  onChange,
  confirmLabel = '保存',
  cancelLabel = '取消',
  placeholder,
  inputLabel = '名称',
  busy = false,
  disabled = false,
}: PromptDialogProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open]);

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
            disabled={busy || disabled}
            className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {confirmLabel}
          </button>
        </>
      }
    >
      <label className="block">
        <span className="block text-[11px] uppercase tracking-wider text-black/55">
          {inputLabel}
        </span>
        <input
          ref={inputRef}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !busy && !disabled) {
              e.preventDefault();
              void onConfirm();
            }
          }}
          placeholder={placeholder}
          className="mt-2 w-full rounded-xl border border-black/10 px-3 py-2.5 text-sm outline-none transition focus:border-black/30"
        />
      </label>
    </AppDialog>
  );
}
