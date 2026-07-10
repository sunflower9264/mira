// Overlays atop React Flow: empty-state hint, bottom-right zoom controls, top toolbar.
// Kept separate so they can share Panel slots without polluting Canvas.tsx.

import { Panel, useReactFlow } from '@xyflow/react';
import { useState } from 'react';
import { useEditorStore } from '../../stores/useEditorStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { NodeToolbar } from './NodeToolbar';
import { NlInputBar } from './NlInputBar';
import { LoadingOverlay } from '../common/LoadingOverlay';
import { FullscreenIcon, MagicWandIcon, PlusIcon, RedoIcon, UndoIcon } from '../common/Icons';
import type { GraphNodeSizeMap } from '../../types';

export function CanvasOverlay({
  toast,
  showToast,
}: {
  toast: string | null;
  showToast: (message: string) => void;
}) {
  const [beautifying, setBeautifying] = useState(false);
  const app = useEditorStore((s) => s.app);
  const beautifyLayout = useEditorStore((s) => s.beautifyLayout);
  const undo = useEditorStore((s) => s.undo);
  const redo = useEditorStore((s) => s.redo);
  const { getNodes, zoomIn, zoomOut, fitView } = useReactFlow();
  const isEmpty = (app?.graph.nodes.length ?? 0) === 0;
  const handleBeautifyLayout = async () => {
    if (isEmpty || beautifying) return;
    const nodeSizes: GraphNodeSizeMap = {};
    for (const node of getNodes()) {
      const width = node.measured?.width ?? node.width;
      const height = node.measured?.height ?? node.height;
      if (typeof width === 'number' && typeof height === 'number' && width > 0 && height > 0) {
        nodeSizes[node.id] = { width, height };
      }
    }
    setBeautifying(true);
    try {
      await beautifyLayout(nodeSizes);
      window.requestAnimationFrame(() => {
        void fitView({ padding: 0.2, maxZoom: 0.9 });
      });
    } catch (error) {
      showCaughtError(error, '美化布局失败', '美化布局失败');
    } finally {
      setBeautifying(false);
    }
  };

  return (
    <>
      <LoadingOverlay
        show={beautifying}
        message="正在美化布局，请稍候..."
        className="z-20"
      />

      <Panel position="top-center" className="!top-3">
        <div className="flex items-center justify-center gap-2">
          <NodeToolbar onToast={showToast} />
          <button
            type="button"
            className="grid h-9 w-9 place-items-center rounded-full border border-black/5 bg-white shadow-pill text-black/75 hover:bg-black/[0.04] hover:text-black disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-white"
            aria-label="美化布局"
            title={beautifying ? '美化布局中' : '美化布局'}
            disabled={isEmpty || beautifying}
            onClick={handleBeautifyLayout}
          >
            <MagicWandIcon className={`w-4 h-4 ${beautifying ? 'animate-pulse' : ''}`} />
          </button>
        </div>
      </Panel>

      {toast && (
        <Panel position="top-center" className="!top-14">
          <div className="rounded-full bg-black px-3 py-1.5 text-xs text-white shadow-pill">
            {toast}
          </div>
        </Panel>
      )}

      {isEmpty && (
        <Panel position="top-left" className="!left-8 !top-20">
          <div className="empty-canvas-hint">
            <div className="sticky-note">添加一个步骤开始</div>
          </div>
        </Panel>
      )}

      {isEmpty && (
        <Panel position="top-center" className="!top-[42%] pointer-events-none">
          <div className="text-center">
            <div className="text-4xl font-semibold tracking-tight">来搭建你的应用</div>
          </div>
        </Panel>
      )}

      <Panel position="bottom-right" className="!bottom-24 !right-3">
        <div className="flex flex-col items-center gap-1 bg-white/85 backdrop-blur rounded-full shadow-pill border border-black/5 p-1">
          <button className="p-1.5 rounded-full hover:bg-black/5" aria-label="适应视图" onClick={() => fitView({ padding: 0.2, maxZoom: 0.9 })}>
            <FullscreenIcon className="w-4 h-4" />
          </button>
          <button className="p-1.5 rounded-full hover:bg-black/5" aria-label="放大" onClick={() => zoomIn()}>
            <PlusIcon className="w-4 h-4" />
          </button>
          <button className="p-1.5 rounded-full hover:bg-black/5" aria-label="缩小" onClick={() => zoomOut()}>
            <span className="block w-4 h-4 text-center leading-none">−</span>
          </button>
          <div className="h-px w-5 bg-black/10 my-1" />
          <button className="p-1.5 rounded-full hover:bg-black/5" aria-label="撤销" onClick={undo}>
            <UndoIcon className="w-4 h-4" />
          </button>
          <button className="p-1.5 rounded-full hover:bg-black/5" aria-label="重做" onClick={redo}>
            <RedoIcon className="w-4 h-4" />
          </button>
        </div>
      </Panel>

      <Panel position="bottom-center" className="!bottom-4 w-full max-w-[560px] px-4">
        <NlInputBar empty={isEmpty} />
      </Panel>
    </>
  );
}
