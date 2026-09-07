import { useState } from 'react';
import { Dialog, DialogBackdrop, DialogPanel, DialogTitle } from '@headlessui/react';
import { Maximize2, X } from 'lucide-react';
import type { RunArtifact } from '../../types';
import { HtmlOutputFrame } from './HtmlOutputFrame';

export function HtmlOutputPreview({ html, artifacts, title = '输出预览' }: { html: string; artifacts?: RunArtifact[]; title?: string }) {
  const [expanded, setExpanded] = useState(false);
  return <section className="min-w-0 bg-white">
    <header className="flex h-11 items-center justify-between gap-2 border-b border-black/5 px-3">
      <span className="min-w-0 truncate text-xs font-medium text-black/50">{title}</span>
      <button type="button" onClick={() => setExpanded(true)} className="inline-flex h-11 shrink-0 items-center gap-1.5 rounded-full px-2 text-xs text-black/60 hover:bg-black/5" aria-label="全屏预览"><Maximize2 className="h-4 w-4" />全屏</button>
    </header>
    {!expanded ? <HtmlOutputFrame html={html} artifacts={artifacts} title={title} className="block w-full border-0 bg-white" /> : null}
    <Dialog open={expanded} onClose={() => setExpanded(false)} className="relative z-[90]">
      <DialogBackdrop className="fixed inset-0 bg-white" />
      <DialogPanel className="fixed inset-0 flex flex-col bg-white pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-black/5 px-4">
          <DialogTitle className="min-w-0 flex-1 truncate text-sm font-medium">{title}</DialogTitle>
          <button type="button" onClick={() => setExpanded(false)} aria-label="关闭全屏预览" className="grid h-11 w-11 shrink-0 place-items-center rounded-full text-black/60 hover:bg-black/5"><X className="h-5 w-5" /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto overscroll-contain" aria-label="全屏预览内容">
          <HtmlOutputFrame html={html} artifacts={artifacts} title={title} className="block w-full border-0 bg-white" />
        </div>
      </DialogPanel>
    </Dialog>
  </section>;
}
