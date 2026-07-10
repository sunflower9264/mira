import { useEffect, type ReactNode } from 'react';
import { CloseIcon } from './Icons';

interface DrawerProps {
  open: boolean;
  onClose(): void;
  title?: ReactNode;
  side?: 'left' | 'right';
  width?: string;
  children: ReactNode;
}

export function Drawer({
  open,
  onClose,
  title,
  side = 'left',
  width = 'w-80',
  children,
}: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  const isLeft = side === 'left';
  const positionClass = isLeft ? 'left-0' : 'right-0';
  const offscreenClass = isLeft ? '-translate-x-full' : 'translate-x-full';

  return (
    <div
      aria-hidden={!open}
      className={`absolute inset-0 z-40 ${open ? 'pointer-events-auto' : 'pointer-events-none'}`}
    >
      <div
        onClick={onClose}
        className={`absolute inset-0 bg-black/30 backdrop-blur-[1px] transition-opacity duration-200 ${
          open ? 'opacity-100' : 'opacity-0'
        }`}
      />
      <aside
        role="dialog"
        aria-modal="true"
        className={`absolute top-0 ${positionClass} flex h-full ${width} max-w-full flex-col border-${isLeft ? 'r' : 'l'} border-black/10 bg-white shadow-card transition-transform duration-200 ease-out ${
          open ? 'translate-x-0' : offscreenClass
        }`}
      >
        <header className="flex h-12 items-center justify-between border-b border-black/5 px-4">
          <div className="text-sm font-medium text-black">{title}</div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="rounded-full p-1.5 text-black/55 transition hover:bg-black/5"
          >
            <CloseIcon className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}
