import { useEffect, useRef } from 'react';
import {
  Description,
  Dialog,
  DialogBackdrop,
  DialogPanel,
  DialogTitle,
} from '@headlessui/react';

interface AppDialogProps {
  open: boolean;
  onClose(): void;
  title: string;
  description?: React.ReactNode;
  children?: React.ReactNode;
  footer?: React.ReactNode;
  widthClassName?: string;
  dismissible?: boolean;
}

export function AppDialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  widthClassName = 'max-w-md',
  dismissible = true,
}: AppDialogProps) {
  const previousOverflowRef = useRef<string>('');

  useEffect(() => {
    if (!open) return;
    previousOverflowRef.current = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflowRef.current;
    };
  }, [open]);

  return (
    <Dialog open={open} onClose={dismissible ? onClose : () => undefined} className="relative z-[80]">
      <DialogBackdrop className="fixed inset-0 bg-black/35 backdrop-blur-[2px]" />
      <div className="fixed inset-0 overflow-y-auto">
        <div className="flex min-h-full items-center justify-center p-4">
          <DialogPanel
            className={`w-full ${widthClassName} rounded-[24px] bg-white p-6 shadow-[0_20px_70px_rgba(0,0,0,0.18)] ring-1 ring-black/5`}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <DialogTitle className="text-lg font-semibold tracking-tight text-black">
                  {title}
                </DialogTitle>
                {description ? (
                  <Description className="mt-2 text-sm leading-6 text-black/65">
                    {description}
                  </Description>
                ) : null}
              </div>
              <button
                type="button"
                onClick={onClose}
                className="rounded-full p-1.5 text-black/45 transition hover:bg-black/5 hover:text-black/70"
                aria-label="关闭弹窗"
              >
                ✕
              </button>
            </div>
            {children ? <div className="mt-5">{children}</div> : null}
            {footer ? <div className="mt-6 flex items-center justify-end gap-3">{footer}</div> : null}
          </DialogPanel>
        </div>
      </div>
    </Dialog>
  );
}
