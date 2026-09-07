import { useState } from 'react';
import { Dialog, DialogBackdrop, DialogPanel, DialogTitle } from '@headlessui/react';
import { Download, Minimize2, X, ZoomIn } from 'lucide-react';

export function ImagePreview({ src, name, className }: { src: string; name: string; className: string }) {
  const [open, setOpen] = useState(false);
  const [zoomed, setZoomed] = useState(false);

  return <>
    <button type="button" onClick={() => { setZoomed(false); setOpen(true); }} className="block w-full cursor-zoom-in bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2" aria-label={`查看全图：${name}`}>
      <img src={src} alt={name} className={className} />
    </button>
    <Dialog open={open} onClose={() => setOpen(false)} className="relative z-[90]">
      <DialogBackdrop className="fixed inset-0 bg-white" />
      <DialogPanel className="fixed inset-0 flex flex-col bg-white pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] text-black">
        <div className="flex shrink-0 items-center gap-2 border-b border-black/5 px-4 py-2">
          <DialogTitle className="min-w-0 flex-1 truncate text-sm font-medium">{name}</DialogTitle>
          <button type="button" onClick={() => setZoomed((value) => !value)} aria-label={zoomed ? '适应屏幕' : '放大图片'} aria-pressed={zoomed} className="grid h-11 w-11 shrink-0 place-items-center rounded-full hover:bg-black/5">
            {zoomed ? <Minimize2 className="h-5 w-5" /> : <ZoomIn className="h-5 w-5" />}
          </button>
          <a href={src} download={name} aria-label={`下载 ${name}`} className="grid h-11 w-11 shrink-0 place-items-center rounded-full hover:bg-black/5"><Download className="h-5 w-5" /></a>
          <button type="button" onClick={() => setOpen(false)} aria-label="关闭全图" className="grid h-11 w-11 shrink-0 place-items-center rounded-full hover:bg-black/5"><X className="h-5 w-5" /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto overscroll-contain p-2 sm:p-6">
          <img src={src} alt={name} className={zoomed ? 'block h-auto w-auto min-w-full max-w-none' : 'h-full w-full object-contain'} />
        </div>
      </DialogPanel>
    </Dialog>
  </>;
}
