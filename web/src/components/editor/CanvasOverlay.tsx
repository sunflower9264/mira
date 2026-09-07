// Overlays atop React Flow: empty-state hint, bottom-right zoom controls, top toolbar.
// Kept separate so they can share Panel slots without polluting Canvas.tsx.

import { Panel, useReactFlow } from '@xyflow/react';
import { useState } from 'react';
import { useEditorStore } from '../../stores/useEditorStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import { NodeToolbar } from './NodeToolbar';
import { NlInputBar } from './NlInputBar';
import { LoadingOverlay } from '../common/LoadingOverlay';
import { FullscreenIcon, MagicWandIcon, PlusIcon, RedoIcon, UndoIcon, TrashIcon, CloseIcon } from '../common/Icons';
import type { GraphNodeSizeMap } from '../../types';

export function CanvasOverlay({
  toast,
  showToast,
  mobile = false,
  onEditStep,
  hasSelection = false,
  touchSelecting = false,
  onToggleTouchSelecting,
  hideNlInput = false,
}: {
  hideNlInput?: boolean;
  mobile?: boolean;
  onEditStep?: () => void;
  hasSelection?: boolean;
  touchSelecting?: boolean;
  onToggleTouchSelecting?: () => void;
  toast: string | null;
  showToast: (message: string) => void;
}) {
  const [beautifying, setBeautifying] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const selectedIds = useEditorStore((s) => s.selectedIds);
  const history = useEditorStore((s) => s.history);
  const app = useEditorStore((s) => s.app);
  const beautifyLayout = useEditorStore((s) => s.beautifyLayout);
  const undo = useEditorStore((s) => s.undo);
  const redo = useEditorStore((s) => s.redo);
  const { getNodes, getEdges, deleteElements, zoomIn, zoomOut, fitView } = useReactFlow();
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

      {mobile ? (
        <Panel position="top-center" style={{ transform: 'translateX(-50%)' }} className="!top-3 !m-0 w-full px-3">
          <div className="flex items-center justify-between gap-1 rounded-2xl border border-black/10 bg-white p-1 shadow-sm">
            <button onClick={() => setAddOpen(!addOpen)} aria-expanded={addOpen} className="flex h-10 items-center gap-1 rounded-xl bg-black px-3 text-sm text-white">
              {addOpen ? <CloseIcon className="h-4 w-4" /> : <PlusIcon className="h-4 w-4" />}步骤
            </button>
            <button className="grid h-10 w-10 place-items-center rounded-xl disabled:opacity-25" aria-label="撤销" disabled={!history.past.length} onClick={undo}><UndoIcon className="h-4 w-4" /></button>
            <button className="grid h-10 w-10 place-items-center rounded-xl disabled:opacity-25" aria-label="重做" disabled={!history.future.length} onClick={redo}><RedoIcon className="h-4 w-4" /></button>
            <button className="grid h-10 w-10 place-items-center rounded-xl disabled:opacity-25" aria-label="美化布局" disabled={isEmpty || beautifying} onClick={handleBeautifyLayout}><MagicWandIcon className="h-4 w-4" /></button>
            <button className="grid h-10 w-10 place-items-center rounded-xl" aria-label="适应视图" onClick={() => fitView({ padding: 0.2, maxZoom: 0.9 })}><FullscreenIcon className="h-4 w-4" /></button>
          </div>
          {addOpen && <div className="mt-2"><NodeToolbar mobile onToast={showToast} onAdded={() => setAddOpen(false)} /></div>}
          {hasSelection && !addOpen && (
            <div className="mt-2 flex items-center justify-between gap-2 rounded-xl border border-black/10 bg-white p-1 shadow-sm">
              <button disabled={selectedIds.length !== 1} onClick={onEditStep} className="min-w-0 flex-1 truncate px-3 py-2 text-left text-sm font-medium disabled:text-black/40">{selectedIds.length === 1 ? '编辑步骤' : selectedIds.length > 1 ? `已选择 ${selectedIds.length} 个步骤` : '已选择连线'}</button>
              <button aria-label="删除所选节点或连线" className="grid h-10 w-10 shrink-0 place-items-center rounded-lg text-red-600" onClick={() => void deleteElements({ nodes: getNodes().filter((node) => node.selected), edges: getEdges().filter((edge) => edge.selected) })}><TrashIcon className="h-4 w-4" /></button>
            </div>
          )}
        </Panel>
      ) : <Panel position="top-center" style={{ transform: 'translateX(-50%)' }} className="!top-3 !m-0 w-full max-w-[580px] px-3">
        <div className="flex items-start justify-center gap-2">
          <NodeToolbar onToast={showToast} />
          <button
            type="button"
            className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full border border-black/5 bg-white shadow-pill text-black/75 hover:bg-black/[0.04] hover:text-black disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-white"
            aria-label="美化布局"
            title={beautifying ? '美化布局中' : '美化布局'}
            disabled={isEmpty || beautifying}
            onClick={handleBeautifyLayout}
          >
            <MagicWandIcon className={`w-4 h-4 ${beautifying ? 'animate-pulse' : ''}`} />
          </button>
        </div>
      </Panel>}

      {toast && (
        <Panel position="top-center" className="!top-32">
          <div className="rounded-full bg-black px-3 py-1.5 text-xs text-white shadow-pill">
            {toast}
          </div>
        </Panel>
      )}

      {isEmpty && !mobile && (
        <Panel position="top-left" className="!left-8 !top-20">
          <div className="empty-canvas-hint">
            <div className="sticky-note">添加一个步骤开始</div>
          </div>
        </Panel>
      )}

      {isEmpty && (
        <Panel position="top-center" className="!top-[42%] pointer-events-none">
          <div className="text-center">
            <div className={`${mobile ? 'text-2xl' : 'text-4xl'} font-semibold tracking-tight`}>来搭建你的应用</div>
          </div>
        </Panel>
      )}

      {mobile ? <Panel position="bottom-right" className="!bottom-24 !right-1">
        <div className="flex rounded-xl border border-black/10 bg-white shadow-sm">
          <button aria-label="多选步骤" aria-pressed={touchSelecting} onClick={onToggleTouchSelecting} className={`min-h-10 rounded-l-xl border-r border-black/10 px-3 text-xs font-medium ${touchSelecting ? 'bg-black text-white' : 'text-black/70'}`}>{touchSelecting ? '完成多选' : '多选'}</button>
          <button aria-label="缩小" onClick={() => zoomOut()} className="h-10 w-10 text-xl">−</button>
          <button aria-label="放大" onClick={() => zoomIn()} className="grid h-10 w-10 place-items-center"><PlusIcon className="h-4 w-4" /></button>
        </div>
      </Panel> : <Panel position="bottom-right" className="!bottom-24 !right-3">
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
      </Panel>}

      {!hideNlInput && <Panel position="bottom-center" style={{ transform: 'translateX(-50%)' }} className="!bottom-3 !m-0 w-full max-w-[560px] px-3">
        <NlInputBar empty={isEmpty} />
      </Panel>}
    </>
  );
}
