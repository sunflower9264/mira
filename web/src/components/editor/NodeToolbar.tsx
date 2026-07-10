import { useEffect, useRef, useState } from 'react';
import { useReactFlow } from '@xyflow/react';
import { useEditorStore } from '../../stores/useEditorStore';
import type { AssetNode as TAsset, NodeType, WorkflowNode } from '../../types';
import {
  BranchIcon,
  BrushIcon,
  FileIcon,
  InputIcon,
  LinkIcon,
  OutputIcon,
  PlusIcon,
  SparkleIcon,
  TextIcon,
} from '../common/Icons';

const ITEMS = [
  { type: 'user_input' as const, label: '用户输入', icon: InputIcon },
  { type: 'generate' as const, label: '生成', icon: SparkleIcon },
  { type: 'condition' as const, label: '判断', icon: BranchIcon },
  { type: 'output' as const, label: '输出', icon: OutputIcon },
];

export const TOOLBAR_NODE_DRAG_TYPE = 'application/x-ai-mira-node-type';
const NODE_HALF_WIDTH = 110;
const NODE_HALF_HEIGHT = 40;

type AssetKind = TAsset['asset_kind'];

const ASSET_KINDS: { kind: AssetKind; label: string; icon: typeof TextIcon }[] = [
  { kind: 'text', label: '文本', icon: TextIcon },
  { kind: 'url', label: '链接', icon: LinkIcon },
  { kind: 'file', label: '文件', icon: FileIcon },
  { kind: 'drawing', label: '画板', icon: BrushIcon },
];

const titleOf: Record<AssetKind, string> = {
  text: '文本',
  url: '链接',
  file: '文件',
  drawing: '画板',
};

function singletonNodeMessage(type: NodeType): string | null {
  if (type === 'user_input') return '一个工作流只能有一个用户输入节点';
  if (type === 'output') return '一个工作流只能有一个输出节点';
  return null;
}

export function NodeToolbar({ onToast }: { onToast?: (message: string) => void }) {
  const toolbarRef = useRef<HTMLDivElement | null>(null);
  const { screenToFlowPosition } = useReactFlow();
  const app = useEditorStore((s) => s.app);
  const addNode = useEditorStore((s) => s.addNode);
  const existingTypes = new Set(app?.graph.nodes.map((node) => node.type) ?? []);

  const positionAtCanvasCenter = () => {
    const flowRoot = toolbarRef.current?.closest('.react-flow');
    const rect = flowRoot?.getBoundingClientRect();
    if (!rect) return undefined;
    const center = screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
    return { x: center.x - NODE_HALF_WIDTH, y: center.y - NODE_HALF_HEIGHT };
  };

  const addNodeAtCenter = (type: NodeType, init?: Partial<WorkflowNode>) => {
    const position = positionAtCanvasCenter();
    return addNode(type, { ...init, ...(position ? { position } : {}) });
  };

  const handlePickAsset = (kind: AssetKind) => {
    const init =
      kind === 'file'
        ? { asset_kind: kind, title: titleOf[kind], uploads: [] }
        : kind === 'drawing'
          ? { asset_kind: kind, title: titleOf[kind], upload: null }
          : kind === 'url'
            ? { asset_kind: kind, title: titleOf[kind], urls: [] }
            : { asset_kind: kind, title: titleOf[kind], content: '' };
    addNodeAtCenter('asset', init as Partial<WorkflowNode>);
  };

  return (
    <div ref={toolbarRef} className="mira-node-toolbar nodrag min-w-[520px] bg-white rounded-full shadow-pill flex flex-nowrap items-center justify-center gap-2 px-3 py-1 border border-black/5">
      {ITEMS.map((it) => (
        <ToolbarNodeButton
          key={it.type}
          type={it.type}
          label={it.label}
          icon={it.icon}
          blockedMessage={existingTypes.has(it.type) ? singletonNodeMessage(it.type) : null}
          onAdd={() => addNodeAtCenter(it.type)}
          onToast={onToast}
        />
      ))}
      <AssetDropdown onPick={handlePickAsset} />
    </div>
  );
}

function ToolbarNodeButton({
  type,
  label,
  icon: Icon,
  blockedMessage,
  onAdd,
  onToast,
}: {
  type: NodeType;
  label: string;
  icon: typeof InputIcon;
  blockedMessage: string | null;
  onAdd: () => WorkflowNode;
  onToast?: (message: string) => void;
}) {
  const blocked = Boolean(blockedMessage);
  return (
    <button
      type="button"
      draggable={!blocked}
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1.5 text-sm ${
        blocked ? 'cursor-not-allowed text-black/35' : 'hover:bg-black/5'
      }`}
      title={blockedMessage ?? label}
      aria-disabled={blocked}
      onClick={() => {
        if (blockedMessage) {
          onToast?.(blockedMessage);
          return;
        }
        onAdd();
      }}
      onDragStart={(e) => {
        if (blockedMessage) {
          e.preventDefault();
          onToast?.(blockedMessage);
          return;
        }
        e.dataTransfer.effectAllowed = 'copy';
        e.dataTransfer.setData(TOOLBAR_NODE_DRAG_TYPE, type);
        e.dataTransfer.setData('text/plain', label);
      }}
    >
      <Icon className="w-4 h-4" />
      {label}
    </button>
  );
}

function AssetDropdown({ onPick }: { onPick: (kind: AssetKind) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-1.5 rounded-full text-sm hover:bg-black/5"
        onClick={() => setOpen((v) => !v)}
      >
        <PlusIcon className="w-4 h-4" />
        添加素材
      </button>
      {open && (
        <div className="absolute top-full mt-1 left-0 bg-white border border-black/10 rounded-xl shadow-lg p-1 z-30 w-36">
          {ASSET_KINDS.map((k) => (
            <button
              key={k.kind}
              type="button"
              className="w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm hover:bg-black/5 text-left"
              onClick={() => {
                setOpen(false);
                onPick(k.kind);
              }}
            >
              <k.icon className="w-4 h-4" />
              {k.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
