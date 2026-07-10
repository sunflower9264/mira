// Wraps React Flow. The store is the source of truth, but we run React Flow in
// *uncontrolled* mode (useNodesState / useEdgesState) so position changes during
// a drag flow into local state at 60 fps — otherwise nodes appear pinned because
// app.graph.nodes only updates on drag-stop, see PRD §7.2. The store is touched
// on structural events (drag-stop, delete, connect) so undo captures the
// pre-drag graph correctly.

import { useCallback, useEffect, useRef, useState, type CSSProperties, type DragEvent as ReactDragEvent, type MouseEvent as ReactMouseEvent } from 'react';
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  BaseEdge,
  ConnectionMode,
  ConnectionLineType,
  EdgeLabelRenderer,
  MarkerType,
  SelectionMode,
  ReactFlowProvider,
  getBezierPath,
  useReactFlow,
  useNodesState,
  useEdgesState,
  type Connection,
  type Edge as RFEdge,
  type EdgeProps,
  type Node as RFNode,
  type NodeChange,
} from '@xyflow/react';
import { useEditorStore } from '../../stores/useEditorStore';
import { UserInputNodeView } from './nodes/UserInputNode';
import { GenerateNodeView } from './nodes/GenerateNode';
import { OutputNodeView } from './nodes/OutputNode';
import { AssetNodeView } from './nodes/AssetNode';
import { ConditionNodeView } from './nodes/ConditionNode';
import { CanvasOverlay } from './CanvasOverlay';
import { uuid } from '../../lib/utils';
import type { ConditionNode, NodeType, WorkflowEdge, WorkflowNode } from '../../types';
import { CONDITION_DEFAULT_BRANCH_KEY } from '../../types';

const TOOLBAR_NODE_DRAG_TYPE = 'application/x-ai-mira-node-type';
const DRAGGABLE_NODE_TYPES: NodeType[] = ['user_input', 'generate', 'output', 'condition'];
const NODE_HALF_WIDTH = 110;
const NODE_HALF_HEIGHT = 40;
const DEFAULT_EDGE_STYLE = { stroke: '#94a3b8', strokeWidth: 2, strokeDasharray: '6 5' };
const SELECTED_EDGE_STYLE = { stroke: '#111827', strokeWidth: 2.5 };
const EDGE_MARKER = { type: MarkerType.ArrowClosed, width: 18, height: 18, color: '#94a3b8' };
const SELECTED_EDGE_MARKER = { type: MarkerType.ArrowClosed, width: 18, height: 18, color: '#111827' };
const CONDITION_LABEL_STYLE = { fill: '#475569', fontSize: 10, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' };
const CONDITION_LABEL_BG_STYLE = { fill: '#ffffff', fillOpacity: 0.95 };
const CONDITION_LABEL_BG_PADDING: [number, number] = [6, 3];
const CONDITION_BRANCH_EDGE_TYPE = 'condition-branch';
const CONDITION_LABEL_OFFSET_X = 56;
const EMPTY_NODES: WorkflowNode[] = [];
const EMPTY_EDGES: WorkflowEdge[] = [];

type ConditionBranchEdgeData = { label?: string };
type ConditionBranchEdge = RFEdge<ConditionBranchEdgeData, typeof CONDITION_BRANCH_EDGE_TYPE>;

const nodeTypes = {
  user_input: UserInputNodeView,
  generate: GenerateNodeView,
  output: OutputNodeView,
  asset: AssetNodeView,
  condition: ConditionNodeView,
};

const conditionBranchLabelStyle: CSSProperties = {
  position: 'absolute',
  transform: 'translate(-50%, -50%)',
};

function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia('(pointer: coarse), (hover: none)');
    const update = () => setCoarse(query.matches);
    update();
    if (typeof query.addEventListener === 'function') {
      query.addEventListener('change', update);
      return () => query.removeEventListener('change', update);
    }
    query.addListener(update);
    return () => query.removeListener(update);
  }, []);

  return coarse;
}

function ConditionBranchEdgeView({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerStart,
  markerEnd,
  style,
  data,
  interactionWidth,
}: EdgeProps<ConditionBranchEdge>) {
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  const label = typeof data?.label === 'string' ? data.label : '';

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={style}
        markerStart={markerStart}
        markerEnd={markerEnd}
        interactionWidth={interactionWidth}
      />
      {label ? (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none max-w-[140px] truncate rounded-full border border-black/10 bg-white/95 px-1.5 py-0.5 font-mono text-[10px] leading-none text-slate-600 shadow-sm"
            style={{
              ...conditionBranchLabelStyle,
              transform: `${conditionBranchLabelStyle.transform} translate(${sourceX + CONDITION_LABEL_OFFSET_X}px, ${sourceY}px)`,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

const edgeTypes = {
  [CONDITION_BRANCH_EDGE_TYPE]: ConditionBranchEdgeView,
};

function toRfNode(n: WorkflowNode, selectedIds: string[], foregroundNodeId: string | null, previous?: RFNode): RFNode {
  const isSelected = selectedIds.includes(n.id);
  return {
    id: n.id,
    type: n.type,
    position: n.position,
    data: n as unknown as Record<string, unknown>,
    selected: isSelected,
    zIndex: n.id === foregroundNodeId ? 1000 : isSelected ? 100 : 0,
    ...(previous?.measured ? { measured: previous.measured } : {}),
    ...(typeof previous?.width === 'number' ? { width: previous.width } : {}),
    ...(typeof previous?.height === 'number' ? { height: previous.height } : {}),
  };
}

function toRfEdge(
  e: { id: string; source: string; target: string; source_handle?: string },
  selectedIds: string[],
  selectedEdgeIds: string[],
  nodes: WorkflowNode[],
  touchCanvas: boolean,
): RFEdge {
  const isSelected = selectedEdgeIds.includes(e.id);
  const isLinkedToSelection = selectedIds.includes(e.source) || selectedIds.includes(e.target);
  const sourceNode = nodes.find((node) => node.id === e.source);
  const label = conditionEdgeLabel(sourceNode, e.source_handle);
  const isConditionBranch = Boolean(label);
  const useSelectedStyle = isSelected || isLinkedToSelection;
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    selected: isSelected,
    sourceHandle: e.source_handle ?? 'source',
    targetHandle: 'target',
    type: isConditionBranch ? CONDITION_BRANCH_EDGE_TYPE : 'default',
    style: useSelectedStyle ? SELECTED_EDGE_STYLE : DEFAULT_EDGE_STYLE,
    markerEnd: useSelectedStyle ? SELECTED_EDGE_MARKER : EDGE_MARKER,
    data: isConditionBranch ? { label: label ?? undefined } : undefined,
    label: isConditionBranch ? undefined : label ?? undefined,
    labelStyle: isConditionBranch ? undefined : CONDITION_LABEL_STYLE,
    labelBgStyle: isConditionBranch ? undefined : CONDITION_LABEL_BG_STYLE,
    labelBgPadding: isConditionBranch ? undefined : CONDITION_LABEL_BG_PADDING,
    labelBgBorderRadius: isConditionBranch ? undefined : 999,
    interactionWidth: touchCanvas ? 32 : 20,
    animated: false,
  };
}

function conditionEdgeLabel(
  sourceData: WorkflowNode | undefined,
  handleId: string | null | undefined,
): string | null {
  if (!sourceData || sourceData.type !== 'condition' || !handleId) return null;
  if (handleId === CONDITION_DEFAULT_BRANCH_KEY) return '其它';
  if (sourceData.mode === 'binary') return handleId;
  const branch = sourceData.branches?.find((b) => b.key === handleId);
  return (branch?.label && branch.label.trim()) || handleId;
}

function isValidGraphConnection(
  conn: { source?: string | null; target?: string | null; sourceHandle?: string | null },
  edges: { source: string; target: string; source_handle?: string | undefined }[],
  nodes: WorkflowNode[],
): boolean {
  if (!conn.source || !conn.target) return false;
  if (conn.source === conn.target) return false;
  // condition 节点必须从某个具体的 source handle 拖出
  const sourceNode = nodes.find((n) => n.id === conn.source);
  const targetNode = nodes.find((n) => n.id === conn.target);
  if (!sourceNode || !targetNode) return false;
  if (sourceNode?.type === 'output') return false;
  if (targetNode?.type === 'user_input' || targetNode?.type === 'asset') return false;
  if (sourceNode?.type === 'condition' && !conn.sourceHandle) return false;
  if (sourceNode?.type === 'condition' && !conditionHandleKeys(sourceNode).has(conn.sourceHandle ?? '')) return false;
  // condition 节点：每个分支（同一 source_handle）最多连一条出边
  if (sourceNode?.type === 'condition') {
    const handle = conn.sourceHandle ?? null;
    if (edges.some((edge) => edge.source === conn.source && (edge.source_handle ?? null) === handle)) {
      return false;
    }
  }
  return !edges.some((edge) => {
    if (edge.source === conn.target && edge.target === conn.source) return true;
    if (edge.source !== conn.source || edge.target !== conn.target) return false;
    // condition 上同一对 (source, target) 的两条边只要 source_handle 不同就允许并存
    if (sourceNode?.type === 'condition') {
      return (edge.source_handle ?? null) === (conn.sourceHandle ?? null);
    }
    return true;
  });
}

function conditionHandleKeys(node: ConditionNode): Set<string> {
  if (node.mode === 'binary') return new Set(['true', 'false']);
  const keys = new Set<string>();
  for (const branch of node.branches ?? []) {
    if (branch && typeof branch.key === 'string' && branch.key) keys.add(branch.key);
  }
  keys.add(CONDITION_DEFAULT_BRANCH_KEY);
  return keys;
}

function syncOutputSources(nodes: WorkflowNode[], edges: WorkflowEdge[]): WorkflowNode[] {
  return nodes.map((node) => {
    if (node.type !== 'output') return node;
    const incoming = edges.filter((edge) => edge.target === node.id);
    if (incoming.length === 0) {
      return node.source_node_id ? { ...node, source_node_id: '' } : node;
    }
    if (node.source_node_id && incoming.some((edge) => edge.source === node.source_node_id)) {
      return node;
    }
    return { ...node, source_node_id: incoming[0].source };
  });
}

function singletonNodeMessage(type: NodeType): string | null {
  if (type === 'user_input') return '一个工作流只能有一个用户输入节点';
  if (type === 'output') return '一个工作流只能有一个输出节点';
  return null;
}

function sameIds(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((id, index) => id === b[index]);
}

function CanvasInner() {
  const touchCanvas = useCoarsePointer();
  const app = useEditorStore((s) => s.app);
  const selectedIds = useEditorStore((s) => s.selectedIds);
  const foregroundNodeId = useEditorStore((s) => s.foregroundNodeId);
  const focusRequest = useEditorStore((s) => s.focusRequest);
  const setGraph = useEditorStore((s) => s.setGraph);
  const addNode = useEditorStore((s) => s.addNode);
  const addEdge = useEditorStore((s) => s.addEdge);
  const setSelectedIds = useEditorStore((s) => s.setSelectedIds);
  const { getNode, getNodes, getZoom, screenToFlowPosition, setCenter } = useReactFlow();

  const [rfNodes, setRfNodes, onNodesChangeRF] = useNodesState<RFNode>([]);
  const [rfEdges, setRfEdges, onEdgesChangeRF] = useEdgesState<RFEdge>([]);
  const [isConnecting, setIsConnecting] = useState(false);
  const [selectedEdgeIds, setSelectedEdgeIds] = useState<string[]>([]);
  const [canvasToast, setCanvasToast] = useState<string | null>(null);

  // Skip external → local sync mid-drag so setRfNodes doesn't fight the pointer.
  const draggingRef = useRef(false);
  const handledFocusSeqRef = useRef(0);
  const toastTimerRef = useRef<number | undefined>(undefined);

  const showCanvasToast = useCallback((message: string) => {
    setCanvasToast(message);
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    toastTimerRef.current = window.setTimeout(() => {
      setCanvasToast(null);
      toastTimerRef.current = undefined;
    }, 2500);
  }, []);

  const setSelectedEdgeIdsIfChanged = useCallback((ids: string[]) => {
    setSelectedEdgeIds((current) => (sameIds(current, ids) ? current : ids));
  }, []);

  useEffect(() => () => {
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
  }, []);

  useEffect(() => {
    if (!app || draggingRef.current) return;
    setRfNodes((currentNodes) => {
      const currentById = new Map(currentNodes.map((node) => [node.id, node]));
      return app.graph.nodes.map((n) => toRfNode(n, selectedIds, foregroundNodeId, currentById.get(n.id)));
    });
  }, [app, selectedIds, foregroundNodeId, setRfNodes]);

  useEffect(() => {
    if (!app) return;
    setRfEdges(app.graph.edges.map((edge) => toRfEdge(edge, selectedIds, selectedEdgeIds, app.graph.nodes, touchCanvas)));
  }, [app, selectedIds, selectedEdgeIds, setRfEdges, touchCanvas]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      onNodesChangeRF(changes);
    },
    [onNodesChangeRF],
  );

  useEffect(() => {
    if (!focusRequest) return;
    if (handledFocusSeqRef.current === focusRequest.seq) return;
    handledFocusSeqRef.current = focusRequest.seq;
    const rfNode = getNode(focusRequest.id);
    const graphNode = app?.graph.nodes.find((n) => n.id === focusRequest.id);
    const position = rfNode?.position ?? graphNode?.position;
    if (!position) return;
    const width = rfNode?.width ?? 220;
    const height = rfNode?.height ?? 80;
    void setCenter(position.x + width / 2, position.y + height / 2, {
      duration: 250,
      zoom: getZoom(),
    });
  }, [app?.graph.nodes, focusRequest, getNode, getZoom, setCenter]);

  const onNodeDragStart = useCallback(() => {
    draggingRef.current = true;
  }, []);

  const onNodeDragStop = useCallback(() => {
    draggingRef.current = false;
    if (!app) return;
    const latestNodes = getNodes();
    const nodes = app.graph.nodes.map((n) => {
      const rf = latestNodes.find((r) => r.id === n.id);
      return rf ? ({ ...n, position: rf.position } as WorkflowNode) : n;
    });
    setGraph({ ...app.graph, nodes });
  }, [app, getNodes, setGraph]);

  const onNodesDelete = useCallback(
    (deleted: RFNode[]) => {
      if (!app) return;
      const ids = new Set(deleted.map((d) => d.id));
      const remainingNodes = app.graph.nodes.filter((n) => !ids.has(n.id));
      const edges = app.graph.edges.filter((e) => !ids.has(e.source) && !ids.has(e.target));
      setSelectedEdgeIds((current) => {
        const next = current.filter((id) => edges.some((edge) => edge.id === id));
        return sameIds(current, next) ? current : next;
      });
      setGraph({
        ...app.graph,
        nodes: syncOutputSources(remainingNodes, edges),
        edges,
      });
    },
    [app, setGraph],
  );

  const onEdgesDelete = useCallback(
    (deleted: RFEdge[]) => {
      if (!app) return;
      const ids = new Set(deleted.map((d) => d.id));
      const edges = app.graph.edges.filter((e) => !ids.has(e.id));
      setSelectedEdgeIds((current) => {
        const next = current.filter((id) => !ids.has(id));
        return sameIds(current, next) ? current : next;
      });
      setGraph({
        ...app.graph,
        nodes: syncOutputSources(app.graph.nodes, edges),
        edges,
      });
    },
    [app, setGraph],
  );

  const onConnect = useCallback(
    (conn: Connection) => {
      if (!app || !isValidGraphConnection(conn, app.graph.edges, app.graph.nodes)) return;
      const sourceNode = app.graph.nodes.find((node) => node.id === conn.source);
      addEdge({
        id: 'e_' + uuid(),
        source: conn.source,
        target: conn.target,
        source_handle: sourceNode?.type === 'condition' ? conn.sourceHandle ?? undefined : undefined,
      });
    },
    [addEdge, app],
  );

  const isValidConnection = useCallback(
    (conn: Connection | RFEdge) => {
      const graphEdges = app?.graph.edges ?? EMPTY_EDGES;
      const graphNodes = app?.graph.nodes ?? EMPTY_NODES;
      return isValidGraphConnection(
        {
          source: 'source' in conn ? conn.source ?? null : null,
          target: 'target' in conn ? conn.target ?? null : null,
          sourceHandle: 'sourceHandle' in conn ? conn.sourceHandle ?? null : null,
        },
        graphEdges,
        graphNodes,
      );
    },
    [app?.graph.edges, app?.graph.nodes],
  );

  const onDragOver = useCallback((event: ReactDragEvent) => {
    if (!event.dataTransfer.types.includes(TOOLBAR_NODE_DRAG_TYPE)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  }, []);

  const onDrop = useCallback(
    (event: ReactDragEvent) => {
      event.preventDefault();
      const type = event.dataTransfer.getData(TOOLBAR_NODE_DRAG_TYPE) as NodeType;
      if (!DRAGGABLE_NODE_TYPES.includes(type)) return;
      const blockedMessage = app?.graph.nodes.some((node) => node.type === type) ? singletonNodeMessage(type) : null;
      if (blockedMessage) {
        showCanvasToast(blockedMessage);
        addNode(type);
        return;
      }
      const point = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      addNode(type, {
        position: { x: point.x - NODE_HALF_WIDTH, y: point.y - NODE_HALF_HEIGHT },
      } as Partial<WorkflowNode>);
    },
    [addNode, app?.graph.nodes, screenToFlowPosition, showCanvasToast],
  );

  const preventContextMenu = useCallback((event: ReactMouseEvent | MouseEvent) => {
    event.preventDefault();
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedIds([]);
    setSelectedEdgeIdsIfChanged([]);
  }, [setSelectedEdgeIdsIfChanged, setSelectedIds]);

  const onSelectionChange = useCallback(
    ({ nodes, edges }: { nodes: RFNode[]; edges: RFEdge[] }) => {
      setSelectedIds(nodes.map((node) => node.id));
      setSelectedEdgeIdsIfChanged(edges.map((edge) => edge.id));
    },
    [setSelectedEdgeIdsIfChanged, setSelectedIds],
  );

  return (
    <ReactFlow
      className={[
        isConnecting ? 'mira-connecting' : '',
        touchCanvas ? 'mira-touch-canvas' : '',
      ].filter(Boolean).join(' ') || undefined}
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChangeRF}
      onNodeDragStart={onNodeDragStart}
      onNodeDragStop={onNodeDragStop}
      onNodesDelete={onNodesDelete}
      onEdgesDelete={onEdgesDelete}
      onConnect={onConnect}
      isValidConnection={isValidConnection}
      onConnectStart={() => setIsConnecting(true)}
      onConnectEnd={() => setIsConnecting(false)}
      onClickConnectStart={() => setIsConnecting(true)}
      onClickConnectEnd={() => setIsConnecting(false)}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onPaneClick={onPaneClick}
      onSelectionChange={onSelectionChange}
      onPaneContextMenu={preventContextMenu}
      onNodeContextMenu={preventContextMenu}
      onEdgeContextMenu={preventContextMenu}
      panOnDrag={touchCanvas}
      panActivationKeyCode={touchCanvas ? null : 'Space'}
      deleteKeyCode={['Delete', 'Backspace']}
      multiSelectionKeyCode={['Control', 'Meta']}
      selectionOnDrag={!touchCanvas}
      selectionMode={SelectionMode.Partial}
      connectionMode={ConnectionMode.Strict}
      connectOnClick
      connectionRadius={touchCanvas ? 56 : 36}
      connectionLineType={ConnectionLineType.Bezier}
      connectionLineStyle={DEFAULT_EDGE_STYLE}
      defaultEdgeOptions={{
        type: 'default',
        style: DEFAULT_EDGE_STYLE,
        markerEnd: EDGE_MARKER,
        interactionWidth: touchCanvas ? 32 : 20,
      }}
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 0.9 }}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Lines} color="#e5e7eb" gap={20} lineWidth={1} />
      <CanvasOverlay toast={canvasToast} showToast={showCanvasToast} />
    </ReactFlow>
  );
}

export function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}
