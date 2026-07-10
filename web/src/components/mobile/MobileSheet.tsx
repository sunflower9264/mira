import { type ReactNode } from 'react';
import { Drawer } from 'vaul';
import { X } from 'lucide-react';

interface MobileSheetProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  title: ReactNode;
  children: ReactNode;
}

export function MobileSheet({ open, onOpenChange, title, children }: MobileSheetProps) {
  return (
    <Drawer.Root open={open} onOpenChange={onOpenChange}>
      <Drawer.Portal>
        <Drawer.Overlay className="fixed inset-0 z-40 bg-black/35 backdrop-blur-[2px]" />
        <Drawer.Content className="fixed inset-x-0 bottom-0 z-50 flex max-h-[88dvh] flex-col rounded-t-[24px] border border-black/10 bg-white shadow-[0_-24px_80px_rgba(0,0,0,0.18)] outline-none">
          <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-black/15" />
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-black/5 px-4">
            <Drawer.Title className="min-w-0 truncate text-base font-semibold text-black">
              {title}
            </Drawer.Title>
            <Drawer.Close asChild>
              <button
                type="button"
                className="grid h-9 w-9 place-items-center rounded-full text-black/55 hover:bg-black/5 hover:text-black"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </Drawer.Close>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-[calc(env(safe-area-inset-bottom)+16px)] pt-4">
            {children}
          </div>
        </Drawer.Content>
      </Drawer.Portal>
    </Drawer.Root>
  );
}
