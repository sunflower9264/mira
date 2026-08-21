import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type ReactNode, type RefObject } from 'react';
import { ReactSketchCanvas, type CanvasPath, type ReactSketchCanvasRef } from 'react-sketch-canvas';
import { useEditorStore } from '../../stores/useEditorStore';
import { useSettingsStore } from '../../stores/useSettingsStore';
import { showCaughtError, showErrorDialog } from '../../stores/useErrorDialogStore';
import type {
  AppAgentKind,
  ArtifactContractKind,
  AssetNode,
  ConditionBranch,
  ConditionNode,
  GenerateNode,
  NodeOutputContract,
  OutputNode,
  ReasoningEffort,
  ToolConfig,
  UploadRef,
  WorkflowEdge,
  UserInputNode,
  WorkflowNode,
  DecisionAnswer,
  DecisionGroup,
} from '../../types';
import { CONDITION_DEFAULT_BRANCH_KEY } from '../../types';
import { cancelPromptAssistant, fetchUploadBlob, generatePromptAssistant, resumePromptAssistant, uploadFile, type PromptAssistantResponse } from '../../lib/api';
import { uuid } from '../../lib/utils';
import {
  defaultReasoningEffortForAgent,
  isAgentEnabled,
  reasoningEffortOptionsForAgent,
  supportedModelsForAgent,
} from '../../lib/agentOptions';
import { SelectDropdown } from '../common/SelectDropdown';
import { LoadingOverlay } from '../common/LoadingOverlay';
import { completeDecisionAnswers, DecisionPromptPanel } from '../common/DecisionPromptPanel';
import {
  buildDecisionSupplementText,
  buildDecisionSubmittedSummary,
  completedDecisionGroupIds,
  type DecisionSubmittedSummary,
  type DecisionSupplementDrafts,
} from '../common/decisionInput';
import { PillInputBar, type PillAttachment } from '../common/PillInputBar';
import { buildPromptFieldTokens, mergePromptFieldTokens, promptFieldOptionLabel } from '../common/promptFields';
import { PromptTokenEditor, type PromptTokenEditorHandle } from '../common/PromptTokenEditor';
import { buildPromptTokens, promptTokenOptionLabel, type PromptTokenDefinition } from '../common/promptTokens';
import { EditIcon, PlusIcon, SendIcon, SparkleIcon, StopIcon, TrashIcon } from '../common/Icons';

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const PROMPT_PLACEHOLDER = '在这里输入内容。';
const DRAWING_SWATCHES = ['#111111', '#ef4444', '#f59e0b', '#10b981', '#2563eb', '#7c3aed'];
const EMPTY_NODES: WorkflowNode[] = [];
const EMPTY_EDGES: WorkflowEdge[] = [];
const EMPTY_TOOLS: ToolConfig[] = [];
const EMPTY_TOOL_IDS: string[] = [];
const NODE_TYPE_LABEL: Record<WorkflowNode['type'], string> = {
  user_input: '用户输入',
  generate: '生成',
  output: '输出',
  asset: '素材',
  condition: '判断',
};
type PatchNode = (id: string, patch: Partial<WorkflowNode>, opts?: { skipHistory?: boolean; skipSave?: boolean }) => void;
type PromptChangeOptions = { skipSave?: boolean };
const TEXT_EDIT_HISTORY_OPTS = { skipHistory: true } as const;

export function StepTab() {
  const nodes = useEditorStore((s) => s.app?.graph.nodes ?? EMPTY_NODES);
  const appAgent = useEditorStore((s) => s.app?.graph.agent ?? '');
  const selectedId = useEditorStore((s) => s.selectedId);
  const selectedIds = useEditorStore((s) => s.selectedIds);
  const patchNode = useEditorStore((s) => s.patchNode);
  const selectedNode = useMemo(
    () => (selectedIds.length === 1 && selectedId ? nodes.find((n) => n.id === selectedId) ?? null : null),
    [nodes, selectedId, selectedIds.length],
  );

  if (selectedIds.length !== 1 || !selectedNode) {
    return (
      <div className="h-full grid place-items-center p-6 text-center text-sm text-black/45">
        <div>
          <div className="text-base font-medium text-black/65">暂无可编辑步骤</div>
          <div className="mt-1">请选择一个节点来编辑它的步骤。</div>
        </div>
      </div>
    );
  }

  switch (selectedNode.type) {
    case 'user_input':
      return <UserInputEditor node={selectedNode} patchNode={patchNode} />;
    case 'generate':
      return <GenerateEditor node={selectedNode} appAgent={appAgent} patchNode={patchNode} />;
    case 'output':
      return <OutputEditor node={selectedNode} appAgent={appAgent} patchNode={patchNode} />;
    case 'asset':
      return <AssetEditor node={selectedNode} patchNode={patchNode} />;
    case 'condition':
      return <ConditionEditor node={selectedNode} appAgent={appAgent} patchNode={patchNode} />;
  }
}

function UserInputEditor({
  node,
  patchNode,
}: {
  node: UserInputNode;
  patchNode: PatchNode;
}) {
  return (
    <EditorShell
      title={node.title}
      fallbackTitle="用户输入"
      bg="bg-nodeYellow"
      onTitleChange={(title) => patchNode(node.id, { title } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
    >
      <textarea
        className={mainTextareaCls}
        value={node.input_schema.placeholder ?? ''}
        placeholder={PROMPT_PLACEHOLDER}
        onChange={(e) =>
          patchNode(
            node.id,
            { input_schema: { ...node.input_schema, placeholder: e.target.value } } as Partial<WorkflowNode>,
            TEXT_EDIT_HISTORY_OPTS,
          )
        }
      />
    </EditorShell>
  );
}

function GenerateEditor({
  node,
  appAgent,
  patchNode,
}: {
  node: GenerateNode;
  appAgent: AppAgentKind;
  patchNode: PatchNode;
}) {
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);
  const promptEditorRef = useRef<PromptTokenEditorHandle>(null);
  const updatePrompt = (prompt: string, opts?: PromptChangeOptions) =>
    patchNode(node.id, { prompt } as Partial<WorkflowNode>, { ...TEXT_EDIT_HISTORY_OPTS, ...opts });
  const modelOptions = useMemo(() => supportedModelsForAgent(settings, appAgent), [settings, appAgent]);
  const reasoningEffortOptions = useMemo(() => reasoningEffortOptionsForAgent(appAgent), [appAgent]);
  const hasSelectedAgent = useMemo(() => isAgentEnabled(settings, appAgent), [settings, appAgent]);

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  return (
    <EditorShell
      title={node.title}
      fallbackTitle="生成"
      bg="bg-nodeBlue"
      onTitleChange={(title) => patchNode(node.id, { title } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
    >
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 [&>*:nth-child(even)]:justify-self-end">
        <Field label="模型">
          <SelectDropdown
            value={node.model ?? ''}
            options={modelOptions}
            onChange={(model) => patchNode(node.id, { model } as Partial<WorkflowNode>)}
            disabled={!hasSelectedAgent}
          />
        </Field>
        <Field label="推理等级">
          <SelectDropdown
            value={node.reasoning_effort ?? defaultReasoningEffortForAgent(appAgent) ?? ''}
            options={reasoningEffortOptions}
            onChange={(reasoning_effort) => patchNode(node.id, { reasoning_effort: reasoning_effort as ReasoningEffort } as Partial<WorkflowNode>)}
            disabled={!hasSelectedAgent || reasoningEffortOptions.length === 0}
            emptyLabel="没有可用推理等级。"
          />
        </Field>
        <OutputContractEditor
          contract={node.output_contract}
          onChange={(output_contract) => patchNode(node.id, { output_contract } as Partial<WorkflowNode>)}
        />
        <PromptToolInsertField node={node} editorRef={promptEditorRef} />
        <PromptStructuredFieldInsert node={node} editorRef={promptEditorRef} />
      </div>
      <label className="inline-flex w-fit items-center gap-2 rounded-full bg-[#F9F9F9] px-3 py-2 text-xs font-medium text-black/65">
        <input
          type="checkbox"
          checked={node.ask_user_enabled !== false}
          onChange={(event) =>
            patchNode(
              node.id,
              { ask_user_enabled: event.target.checked ? undefined : false } as Partial<WorkflowNode>,
            )
          }
          className="h-3.5 w-3.5 accent-black"
        />
        运行前允许追问
      </label>
      {settings && !hasSelectedAgent && <div className="text-xs text-red-600">请先在应用页选择已启用 Agent。</div>}
      <PromptField editorRef={promptEditorRef} node={node} value={node.prompt ?? ''} onChange={updatePrompt} nodeLabel={node.title || '生成'} />
    </EditorShell>
  );
}

function OutputEditor({
  node,
  appAgent,
  patchNode,
}: {
  node: OutputNode;
  appAgent: AppAgentKind;
  patchNode: PatchNode;
}) {
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);
  const promptEditorRef = useRef<PromptTokenEditorHandle>(null);
  const graphNodes = useEditorStore((s) => s.app?.graph.nodes ?? EMPTY_NODES);
  const graphEdges = useEditorStore((s) => s.app?.graph.edges ?? EMPTY_EDGES);
  const updatePrompt = (prompt: string, opts?: PromptChangeOptions) =>
    patchNode(node.id, { prompt } as Partial<WorkflowNode>, { ...TEXT_EDIT_HISTORY_OPTS, ...opts });
  const modelOptions = useMemo(() => supportedModelsForAgent(settings, appAgent), [settings, appAgent]);
  const reasoningEffortOptions = useMemo(() => reasoningEffortOptionsForAgent(appAgent), [appAgent]);
  const hasSelectedAgent = useMemo(() => isAgentEnabled(settings, appAgent), [settings, appAgent]);
  const sourceOptions = useMemo(
    () => {
      const seen = new Set<string>();
      const options: { label: string; value: string }[] = [];
      for (const edge of graphEdges) {
        if (edge.target !== node.id || seen.has(edge.source)) continue;
        seen.add(edge.source);
        const source = graphNodes.find((candidate) => candidate.id === edge.source);
        if (!source) continue;
        const title = source.title?.trim() || NODE_TYPE_LABEL[source.type];
        options.push({ label: `${title} · ${NODE_TYPE_LABEL[source.type]}`, value: source.id });
      }
      return options;
    },
    [graphEdges, graphNodes, node.id],
  );

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  return (
    <EditorShell
      title={node.title}
      fallbackTitle="输出"
      bg="bg-nodeGreen"
      onTitleChange={(title) => patchNode(node.id, { title } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
    >
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 [&>*:nth-child(even)]:justify-self-end">
        <Field label="模型">
          <SelectDropdown
            value={node.model ?? ''}
            options={modelOptions}
            onChange={(model) => patchNode(node.id, { model } as Partial<WorkflowNode>)}
            disabled={!hasSelectedAgent}
          />
        </Field>
        <Field label="推理等级">
          <SelectDropdown
            value={node.reasoning_effort ?? defaultReasoningEffortForAgent(appAgent) ?? ''}
            options={reasoningEffortOptions}
            onChange={(reasoning_effort) => patchNode(node.id, { reasoning_effort: reasoning_effort as ReasoningEffort } as Partial<WorkflowNode>)}
            disabled={!hasSelectedAgent || reasoningEffortOptions.length === 0}
            emptyLabel="没有可用推理等级。"
          />
        </Field>
        <Field label="主输入">
          <SelectDropdown
            value={node.source_node_id ?? ''}
            options={sourceOptions}
            onChange={(source_node_id) => patchNode(node.id, { source_node_id } as Partial<WorkflowNode>)}
            placeholder={sourceOptions.length === 0 ? '暂无上游' : '选择主输入'}
            emptyLabel="先从画布连入一个上游节点。"
            disabled={sourceOptions.length === 0}
          />
        </Field>
        <PromptToolInsertField node={node} editorRef={promptEditorRef} />
        <PromptStructuredFieldInsert node={node} editorRef={promptEditorRef} />
      </div>
      {settings && !hasSelectedAgent && <div className="text-xs text-red-600">请先在应用页选择已启用 Agent。</div>}
      <PromptField editorRef={promptEditorRef} node={node} value={node.prompt ?? ''} onChange={updatePrompt} nodeLabel={node.title || '输出'} />
    </EditorShell>
  );
}

function AssetEditor({
  node,
  patchNode,
}: {
  node: AssetNode;
  patchNode: PatchNode;
}) {
  if (node.asset_kind === 'drawing') {
    return (
      <EditorShell
        title={node.title}
        fallbackTitle="画板"
        bg="bg-nodePurple"
        onTitleChange={(title) => patchNode(node.id, { title } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
      >
        <DrawingField
          upload={node.upload}
          filename={`${node.title?.trim() || 'drawing'}.png`}
          onSave={(upload) => patchNode(node.id, { upload } as Partial<WorkflowNode>)}
        />
      </EditorShell>
    );
  }

  if (node.asset_kind === 'file') {
    return (
      <EditorShell
        title={node.title}
        fallbackTitle="文件"
        bg="bg-nodePurple"
        onTitleChange={(title) => patchNode(node.id, { title } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
      >
        <FileField
          node={node}
          onChange={(uploads) => patchNode(node.id, { uploads } as Partial<WorkflowNode>)}
        />
      </EditorShell>
    );
  }

  if (node.asset_kind === 'url') {
    return (
      <EditorShell
        title={node.title}
        fallbackTitle="链接"
        bg="bg-nodePurple"
        onTitleChange={(title) => patchNode(node.id, { title } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
      >
        <UrlField
          urls={node.urls}
          onChange={(urls) => patchNode(node.id, { urls } as Partial<WorkflowNode>)}
          onTextChange={(urls) => patchNode(node.id, { urls } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
        />
      </EditorShell>
    );
  }

  return (
    <EditorShell
      title={node.title}
      fallbackTitle="文本"
      bg="bg-nodePurple"
      onTitleChange={(title) => patchNode(node.id, { title } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
    >
      <textarea
        className={mainTextareaCls}
        value={node.content}
        placeholder={PROMPT_PLACEHOLDER}
        onChange={(e) => patchNode(node.id, { content: e.target.value } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
      />
    </EditorShell>
  );
}

function ConditionEditor({
  node,
  appAgent,
  patchNode,
}: {
  node: ConditionNode;
  appAgent: AppAgentKind;
  patchNode: PatchNode;
}) {
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);
  const promptEditorRef = useRef<PromptTokenEditorHandle>(null);
  const modelOptions = useMemo(() => supportedModelsForAgent(settings, appAgent), [settings, appAgent]);
  const reasoningEffortOptions = useMemo(() => reasoningEffortOptionsForAgent(appAgent), [appAgent]);
  const hasSelectedAgent = useMemo(() => isAgentEnabled(settings, appAgent), [settings, appAgent]);

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  const updatePrompt = (prompt: string, opts?: PromptChangeOptions) =>
    patchNode(node.id, { prompt } as Partial<WorkflowNode>, { ...TEXT_EDIT_HISTORY_OPTS, ...opts });

  const setMode = (mode: ConditionNode['mode']) => {
    if (mode === node.mode) return;
    if (mode === 'binary') {
      patchNode(node.id, {
        mode: 'binary',
        branches: [{ key: 'true' }, { key: 'false' }],
      } as Partial<WorkflowNode>);
    } else {
      const isDefaultBinary =
        node.branches.length === 2 &&
        node.branches[0]?.key === 'true' &&
        node.branches[1]?.key === 'false';
      const branches: ConditionBranch[] = isDefaultBinary
        ? [
            { key: 'case_a', label: 'A' },
            { key: 'case_b', label: 'B' },
          ]
        : node.branches;
      patchNode(node.id, { mode: 'cases', branches } as Partial<WorkflowNode>);
    }
  };

  const updateBranches = (next: ConditionBranch[]) =>
    patchNode(node.id, { branches: next } as Partial<WorkflowNode>);
  const updateBranchText = (next: ConditionBranch[]) =>
    patchNode(node.id, { branches: next } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS);

  const renderHeaderControls = (
    <div className="grid grid-cols-2 gap-x-4 gap-y-3 [&>*:nth-child(even)]:justify-self-end">
      <Field label="模式">
        <SelectDropdown
          value={node.mode}
          options={[
            { label: '二分支 (true/false)', value: 'binary' },
            { label: '多分支 (cases)', value: 'cases' },
          ]}
          onChange={(value) => setMode(value as ConditionNode['mode'])}
        />
      </Field>
      <Field label="模型">
        <SelectDropdown
          value={node.model ?? ''}
          options={modelOptions}
          onChange={(model) => patchNode(node.id, { model } as Partial<WorkflowNode>)}
          disabled={!hasSelectedAgent}
        />
      </Field>
      <Field label="推理等级">
        <SelectDropdown
          value={node.reasoning_effort ?? defaultReasoningEffortForAgent(appAgent) ?? ''}
          options={reasoningEffortOptions}
          onChange={(reasoning_effort) => patchNode(node.id, { reasoning_effort: reasoning_effort as ReasoningEffort } as Partial<WorkflowNode>)}
          disabled={!hasSelectedAgent || reasoningEffortOptions.length === 0}
          emptyLabel="没有可用推理等级。"
        />
      </Field>
      <PromptToolInsertField node={node} editorRef={promptEditorRef} />
      <PromptStructuredFieldInsert node={node} editorRef={promptEditorRef} />
    </div>
  );

  return (
    <EditorShell
      title={node.title}
      fallbackTitle="判断"
      bg="bg-nodeOrange"
      onTitleChange={(title) => patchNode(node.id, { title } as Partial<WorkflowNode>, TEXT_EDIT_HISTORY_OPTS)}
    >
      {renderHeaderControls}
      {settings && !hasSelectedAgent && <div className="text-xs text-red-600">请先在应用页选择已启用 Agent。</div>}
      {node.mode === 'cases' ? (
        <BranchListEditor branches={node.branches} onChange={updateBranches} onTextChange={updateBranchText} />
      ) : (
        <div className="text-[11px] text-black/45">
          binary 模式固定为 <span className="font-mono text-black/60">true</span> /{' '}
          <span className="font-mono text-black/60">false</span> 两个分支；模型输出无法识别时归 false。
        </div>
      )}
      <PromptField editorRef={promptEditorRef} node={node} value={node.prompt ?? ''} onChange={updatePrompt} nodeLabel={node.title || '判断'} />
    </EditorShell>
  );
}

const KEY_PATTERN = /^[a-zA-Z0-9_]+$/;

function BranchListEditor({
  branches,
  onChange,
  onTextChange,
}: {
  branches: ConditionBranch[];
  onChange: (next: ConditionBranch[]) => void;
  onTextChange: (next: ConditionBranch[]) => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const updateAt = (index: number, patch: Partial<ConditionBranch>) => {
    const next = branches.map((branch, i) => (i === index ? { ...branch, ...patch } : branch));
    // 校验 key 唯一、字符合法、非保留
    const keys = next.map((b) => b.key.trim());
    if (keys.some((key) => !key)) {
      setError('分支 key 不能为空');
    } else if (keys.some((key) => key === CONDITION_DEFAULT_BRANCH_KEY)) {
      setError(`保留 key "${CONDITION_DEFAULT_BRANCH_KEY}" 不能用作自定义分支`);
    } else if (keys.some((key) => !KEY_PATTERN.test(key))) {
      setError('分支 key 只能包含字母、数字和下划线');
    } else if (new Set(keys).size !== keys.length) {
      setError('分支 key 不能重复');
    } else {
      setError(null);
    }
    onTextChange(next);
  };

  const addBranch = () => {
    const used = new Set(branches.map((b) => b.key));
    let i = branches.length + 1;
    let key = `case_${i}`;
    while (used.has(key)) {
      i += 1;
      key = `case_${i}`;
    }
    onChange([...branches, { key, label: '' }]);
  };

  const removeAt = (index: number) => {
    if (branches.length <= 2) {
      setError('至少需要保留两个分支');
      return;
    }
    const next = branches.filter((_, i) => i !== index);
    setError(null);
    onChange(next);
  };

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wider text-black/45">分支</span>
        <button
          type="button"
          onClick={addBranch}
          className="inline-flex items-center gap-1 rounded-full bg-[#F9F9F9] px-2.5 py-1 text-xs hover:bg-black/[0.04]"
        >
          <PlusIcon className="h-3.5 w-3.5" />
          添加分支
        </button>
      </div>
      <ul className="flex flex-col gap-1.5">
        {branches.map((branch, index) => (
          <li key={index} className="flex items-center gap-2">
            <input
              className="h-9 w-32 rounded-lg bg-[#F9F9F9] px-2 font-mono text-sm outline-none focus:bg-white focus:ring-1 focus:ring-black/15"
              value={branch.key}
              placeholder="key"
              onChange={(e) => updateAt(index, { key: e.target.value.trim() })}
            />
            <input
              className="h-9 flex-1 rounded-lg bg-[#F9F9F9] px-2 text-sm outline-none focus:bg-white focus:ring-1 focus:ring-black/15"
              value={branch.label ?? ''}
              placeholder="显示名（可选）"
              onChange={(e) => updateAt(index, { label: e.target.value })}
            />
            <button
              type="button"
              onClick={() => removeAt(index)}
              className="rounded-full p-1.5 text-black/45 hover:bg-black/5 hover:text-black/70"
              aria-label="删除分支"
              title="删除分支"
            >
              <TrashIcon className="h-3.5 w-3.5" />
            </button>
          </li>
        ))}
      </ul>
      <div className="mt-1.5 text-[11px] text-black/40">
        始终额外存在一个 <span className="font-mono text-black/55">{CONDITION_DEFAULT_BRANCH_KEY}</span>{' '}
        兜底分支（标"其它"），LLM 输出无法识别时走该分支。
      </div>
      {error && <div className="mt-1 text-[11px] text-red-600">{error}</div>}
    </div>
  );
}

type OutputContractOptionValue = 'free' | 'json' | 'html' | `artifact:${ArtifactContractKind}`;

const OUTPUT_CONTRACT_OPTIONS: { label: string; value: OutputContractOptionValue }[] = [
  { label: '普通文本（默认）', value: 'free' },
  { label: '结构化 JSON', value: 'json' },
  { label: 'HTML', value: 'html' },
  { label: '图片', value: 'artifact:image' },
  { label: '代码包', value: 'artifact:code' },
  { label: 'HTML 文件', value: 'artifact:html' },
  { label: 'Markdown 文件', value: 'artifact:markdown' },
  { label: 'CSV', value: 'artifact:csv' },
  { label: 'Excel', value: 'artifact:excel' },
  { label: 'DOCX', value: 'artifact:docx' },
  { label: 'PPT', value: 'artifact:ppt' },
  { label: 'PDF', value: 'artifact:pdf' },
  { label: '压缩包', value: 'artifact:archive' },
  { label: 'ZIP 压缩包', value: 'artifact:zip' },
  { label: '其他文件', value: 'artifact:file' },
];

function OutputContractEditor({
  contract,
  onChange,
}: {
  contract: NodeOutputContract | undefined;
  onChange: (next: NodeOutputContract | undefined) => void;
}) {
  const selectedValue = contractOptionValue(contract);
  const schemaText = JSON.stringify(contract?.type === 'json' ? contract.json_schema : defaultJsonContractSchema(), null, 2);
  const [schemaDraft, setSchemaDraft] = useState(schemaText);

  useEffect(() => {
    setSchemaDraft(schemaText);
  }, [schemaText]);

  const setOption = (value: string) => {
    if (value === selectedValue) return;
    if (value === 'json') {
      onChange({ type: 'json', json_schema: defaultJsonContractSchema() });
      return;
    }
    if (value === 'html') {
      onChange({ type: 'html' });
      return;
    }
    if (value.startsWith('artifact:')) {
      const artifact_kind = value.slice('artifact:'.length) as ArtifactContractKind;
      onChange({ type: 'artifact', artifact_kind });
      return;
    }
    onChange(undefined);
  };

  const updateSchema = (value: string) => {
    setSchemaDraft(value);
    if (!contract || contract.type !== 'json') return;
    try {
      const parsed = JSON.parse(value);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        onChange({ ...contract, json_schema: parsed as Record<string, unknown> });
      }
    } catch {
      return;
    }
  };

  return (
    <div className="col-span-2 grid w-full gap-2">
      <Field label="输出契约">
        <SelectDropdown
          value={selectedValue}
          options={OUTPUT_CONTRACT_OPTIONS}
          onChange={setOption}
        />
      </Field>
      <div className="text-[11px] leading-relaxed text-black/40">
        普通文本适合大多数生成节点；仅当下游需要稳定读取字段、当前节点要直接产出 HTML，或需要生成可下载文件时使用契约。
      </div>
      {contract?.type === 'json' && (
        <div className="grid gap-1.5">
          <div className="text-[11px] leading-relaxed text-black/40">
            高级配置：JSON Schema 会严格校验字段；请为根对象和每个业务字段填写简短准确的中文 title 与 description。
          </div>
          <textarea
            className="min-h-32 w-full resize-y rounded border border-black/10 bg-white px-3 py-2 font-mono text-xs leading-relaxed outline-none focus:border-black/30"
            value={schemaDraft}
            onChange={(event) => updateSchema(event.target.value)}
            spellCheck={false}
          />
        </div>
      )}
    </div>
  );
}

function contractOptionValue(contract: NodeOutputContract | undefined): OutputContractOptionValue {
  if (!contract) return 'free';
  if (contract.type === 'json') return 'json';
  if (contract.type === 'html') return 'html';
  if (contract.type === 'artifact') return `artifact:${contract.artifact_kind ?? 'file'}`;
  return 'free';
}

function defaultJsonContractSchema(): Record<string, unknown> {
  return {
    title: '结构化结果',
    description: '当前节点的结构化输出。',
    type: 'object',
    additionalProperties: false,
    properties: {
      result: {
        title: '结果',
        description: '当前节点生成的主要结果。',
        type: 'string',
      },
    },
    required: ['result'],
  };
}

function PromptToolInsertField({
  node,
  editorRef,
}: {
  node: GenerateNode | OutputNode | ConditionNode;
  editorRef: RefObject<PromptTokenEditorHandle>;
}) {
  const tools = useSettingsStore((s) => s.settings?.tools ?? EMPTY_TOOLS);
  const disabledToolIds = useEditorStore((s) => s.app?.graph.tools?.disabled_tool_ids ?? EMPTY_TOOL_IDS);
  const generating = useEditorStore((s) => s.promptAssistantGenerations[node.id] != null);
  const insertableTokens = useMemo(() => {
    const disabled = new Set(disabledToolIds);
    const availableTools = tools.filter((tool) => tool.enabled && !disabled.has(tool.id));
    const includeSystem = node.type === 'condition' || (node.type === 'generate' && node.ask_user_enabled !== false);
    const kindOrder = { system: 0, skill: 1, mcp: 2 } as const;
    return buildPromptTokens(availableTools, includeSystem).sort(
      (a, b) => kindOrder[a.kind] - kindOrder[b.kind] || a.label.localeCompare(b.label),
    );
  }, [disabledToolIds, node, tools]);

  return (
    <Field label="插入工具" className="col-span-2">
      <SelectDropdown
        value=""
        options={insertableTokens.map((token) => ({ label: promptTokenOptionLabel(token), value: token.value }))}
        onChange={(tokenValue) => editorRef.current?.insertToken(tokenValue)}
        placeholder="选择系统工具、Skill 或 MCP"
        emptyLabel="当前应用没有可用工具。"
        disabled={generating}
        buttonClassName={`${selectButtonCls} w-64`}
        menuClassName="absolute left-0 top-full z-30 mt-1 max-h-64 w-72 overflow-y-auto rounded-xl border border-black/10 bg-white p-1 shadow-lg"
      />
    </Field>
  );
}

function PromptStructuredFieldInsert({
  node,
  editorRef,
}: {
  node: GenerateNode | OutputNode | ConditionNode;
  editorRef: RefObject<PromptTokenEditorHandle>;
}) {
  const graph = useEditorStore((s) => s.app?.graph);
  const generating = useEditorStore((s) => s.promptAssistantGenerations[node.id] != null);
  const choices = useMemo(
    () => buildPromptFieldTokens(graph, node.id).map((field, index) => ({
      field,
      option: {
        label: promptFieldOptionLabel(field),
        value: `field:${index}:${field.sourceNodeId}:${field.value}`,
      },
    })),
    [graph, node.id],
  );

  return (
    <Field label="插入字段" className="col-span-2">
      <SelectDropdown
        value=""
        options={choices.map((choice) => choice.option)}
        onChange={(optionValue) => {
          const choice = choices.find((item) => item.option.value === optionValue);
          if (choice) editorRef.current?.insertToken(choice.field.value);
        }}
        placeholder="选择结构化字段或状态值"
        emptyLabel="当前节点及直接上游没有 JSON 字段。"
        disabled={generating}
        buttonClassName={`${selectButtonCls} w-64`}
        menuClassName="absolute left-0 top-full z-30 mt-1 max-h-64 w-96 overflow-y-auto rounded-xl border border-black/10 bg-white p-1 shadow-lg"
      />
    </Field>
  );
}

function PromptField({
  value,
  onChange,
  node,
  nodeLabel,
  editorRef,
}: {
  value: string;
  onChange: (next: string, opts?: PromptChangeOptions) => void;
  node: GenerateNode | OutputNode | ConditionNode;
  nodeLabel: string;
  editorRef: RefObject<PromptTokenEditorHandle>;
}) {
  const app = useEditorStore((s) => s.app);
  const tools = useSettingsStore((s) => s.settings?.tools ?? EMPTY_TOOLS);
  const flushSave = useEditorStore((s) => s.flushSave);
  const promptGeneration = useEditorStore((s) => s.promptAssistantGenerations[node.id] ?? null);
  const startPromptAssistantGeneration = useEditorStore((s) => s.startPromptAssistantGeneration);
  const finishPromptAssistantGeneration = useEditorStore((s) => s.finishPromptAssistantGeneration);
  const [open, setOpen] = useState(false);
  const [request, setRequest] = useState('');
  const [localError, setLocalError] = useState<string | null>(null);
  const [decisionAnswers, setDecisionAnswers] = useState<DecisionAnswer[]>([]);
  const [activeDecisionGroupId, setActiveDecisionGroupId] = useState('');
  const [activeDecisionGroupIndex, setActiveDecisionGroupIndex] = useState(0);
  const [decisionDrafts, setDecisionDrafts] = useState<DecisionSupplementDrafts>({});
  const [submittedDecisionSummary, setSubmittedDecisionSummary] = useState<DecisionSubmittedSummary | null>(null);
  const [submittingDecision, setSubmittingDecision] = useState(false);
  const mountedRef = useRef(true);
  const generating = promptGeneration !== null;
  const requestValue = promptGeneration?.request ?? request;
  const waitingRequest = promptGeneration?.waitingRequest ?? null;
  const error = localError;
  const completedDecisionGroupIdsValue = waitingRequest
    ? completedDecisionGroupIds(waitingRequest.groups, decisionAnswers)
    : [];
  const completedPromptDecisionAnswers = waitingRequest
    ? completeDecisionAnswers(waitingRequest.groups, decisionAnswers)
    : null;
  const activeDecisionDraft = activeDecisionGroupId ? decisionDrafts[activeDecisionGroupId] : undefined;
  const activeDecisionText = activeDecisionDraft?.text ?? '';
  const activeDecisionAttachments = activeDecisionDraft?.attachments ?? [];
  const activePromptDecisionIsLast = waitingRequest
    ? activeDecisionGroupIndex >= waitingRequest.groups.length - 1
    : false;
  const canSubmitPromptDecision = !!completedPromptDecisionAnswers && activePromptDecisionIsLast && !submittingDecision;
  const showDecisionForm = !!waitingRequest && !submittedDecisionSummary && !submittingDecision;
  const showPromptTextarea = !showDecisionForm;
  const promptTokens = useMemo(() => {
    const disabledToolIds = new Set(app?.graph.tools?.disabled_tool_ids ?? []);
    const availableTools = tools.filter((tool) => tool.enabled && !disabledToolIds.has(tool.id));
    const includeSystem = node.type === 'condition' || (node.type === 'generate' && node.ask_user_enabled !== false);
    const tokens = new Map<string, PromptTokenDefinition>(
      buildPromptTokens(availableTools, includeSystem).map((token) => [token.value, token]),
    );
    for (const field of mergePromptFieldTokens(buildPromptFieldTokens(app?.graph, node.id))) {
      if (!tokens.has(field.value)) tokens.set(field.value, field);
    }
    return [...tokens.values()].sort((a, b) => b.value.length - a.value.length || a.value.localeCompare(b.value));
  }, [app?.graph, node, tools]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    setDecisionAnswers([]);
    setActiveDecisionGroupId(waitingRequest?.groups[0]?.id ?? '');
    setActiveDecisionGroupIndex(0);
    setDecisionDrafts({});
    setSubmittedDecisionSummary(null);
    setSubmittingDecision(false);
  }, [waitingRequest]);

  const isAbortError = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';
  const flushPromptSave = () => {
    if (!generating) void flushSave().catch(() => undefined);
  };

  const cancelGeneration = () => {
    const active = promptGeneration;
    if (!active) return;
    finishPromptAssistantGeneration(node.id, active.generationId);
    void cancelPromptAssistant(active.generationId).catch(() => undefined);
    active.controller.abort();
    setDecisionAnswers([]);
    setActiveDecisionGroupId('');
    setActiveDecisionGroupIndex(0);
    setDecisionDrafts({});
    setSubmittedDecisionSummary(null);
    setSubmittingDecision(false);
  };

  const applyCompletedPrompt = (result: Extract<PromptAssistantResponse, { status: 'completed' }>) => {
    const state = useEditorStore.getState();
    const stateApp = state.app;
    if (!stateApp || stateApp.id !== app?.id) return;
    const currentNode = stateApp.graph.nodes.find((item) => item.id === node.id);
    if (!currentNode) return;
    const patch = { prompt: result.prompt } as Partial<WorkflowNode>;
    if (currentNode.type === 'generate' && result.output_contract) {
      const { validate_office_documents, ...contract } = result.output_contract;
      const nextContract: NodeOutputContract =
        typeof validate_office_documents === 'boolean'
          ? { ...contract, validate_office_documents }
          : contract;
      if (
        validate_office_documents == null &&
        currentNode.output_contract?.type === 'artifact' &&
        currentNode.output_contract.validate_office_documents === true &&
        nextContract.type === 'artifact' &&
        nextContract.artifact_kind === currentNode.output_contract.artifact_kind
      ) {
        nextContract.validate_office_documents = true;
      }
      (patch as Partial<GenerateNode>).output_contract = nextContract;
    }
    state.patchNode(node.id, patch);
    if (mountedRef.current) {
      setRequest('');
      setOpen(false);
      setDecisionAnswers([]);
      setActiveDecisionGroupId('');
      setActiveDecisionGroupIndex(0);
      setDecisionDrafts({});
      setSubmittedDecisionSummary(null);
      setSubmittingDecision(false);
    }
  };

  const updateActiveDecisionDraft = (patch: Partial<{ text: string; attachments: PillAttachment[] }>) => {
    if (!activeDecisionGroupId) return;
    setDecisionDrafts((current) => {
      const previous = current[activeDecisionGroupId] ?? { text: '', attachments: [] };
      return {
        ...current,
        [activeDecisionGroupId]: { ...previous, ...patch },
      };
    });
  };

  const uploadDecisionDrafts = async (
    groups: DecisionGroup[],
    source: DecisionSupplementDrafts,
  ): Promise<{ refs: { id: string; name?: string }[]; drafts: DecisionSupplementDrafts }> => {
    const refs: { id: string; name?: string }[] = [];
    const next: DecisionSupplementDrafts = { ...source };
    for (const group of groups) {
      const draft = next[group.id];
      if (!draft?.attachments.length) continue;
      const attachmentsForGroup = [...draft.attachments];
      for (let i = 0; i < attachmentsForGroup.length; i += 1) {
        const item = attachmentsForGroup[i];
        if (item.uploadId) {
          refs.push({ id: item.uploadId, name: item.name });
          continue;
        }
        if (!item.file) throw new Error(`附件「${item.name}」缺少文件内容`);
        const result = await uploadFile(item.file);
        attachmentsForGroup[i] = { ...item, uploadId: result.id };
        refs.push({ id: result.id, name: item.name });
      }
      next[group.id] = { ...draft, attachments: attachmentsForGroup };
    }
    setDecisionDrafts(next);
    return { refs, drafts: next };
  };

  const handlePromptAssistantResponse = (
    result: PromptAssistantResponse,
    generationId: string,
  ): boolean => {
    const state = useEditorStore.getState();
    const currentGeneration = state.promptAssistantGenerations[node.id];
    const stateApp = state.app;
    const nodeStillExists = !!stateApp && stateApp.id === app?.id && stateApp.graph.nodes.some((item) => item.id === node.id);
    if (!currentGeneration || currentGeneration.generationId !== generationId || !nodeStillExists) return false;
    if (result.status === 'waiting_for_user') {
      startPromptAssistantGeneration({
        ...currentGeneration,
        waitingRequest: result.request,
      });
      setDecisionAnswers([]);
      setSubmittedDecisionSummary(null);
      setSubmittingDecision(false);
      return true;
    }
    if (result.status === 'interrupted') {
      showErrorDialog(result.error || '提示词生成已中断，请重新生成', '提示词生成失败');
      return false;
    }
    applyCompletedPrompt(result);
    return false;
  };

  const generatePrompt = async () => {
    if (promptGeneration) return;
    const appId = app?.id;
    const agent = app?.graph.agent;
    if (!appId) {
      setLocalError('应用尚未加载完成。');
      return;
    }
    if (!agent) {
      setLocalError('请先在应用页选择已启用 Agent。');
      return;
    }
    const generationId = `pa_${uuid()}`;
    const controller = new AbortController();
    const requestText = request.trim();
    setLocalError(null);
    startPromptAssistantGeneration({
      appId,
      nodeId: node.id,
      generationId,
      controller,
      request: requestText,
    });
    let keepSession = false;
    try {
      const result = await generatePromptAssistant({
        app_id: appId,
        generation_id: generationId,
        agent,
        graph: app.graph,
        node_id: node.id,
        user_request: requestText,
        model: node.model,
        reasoning_effort: node.reasoning_effort,
      }, controller.signal);
      keepSession = handlePromptAssistantResponse(result, generationId);
    } catch (e) {
      if (!isAbortError(e) && mountedRef.current) showCaughtError(e, '生成提示词失败', '生成失败');
    } finally {
      if (!keepSession) finishPromptAssistantGeneration(node.id, generationId);
    }
  };

  const submitPromptAssistantDecision = async () => {
    const active = promptGeneration;
    if (!active?.waitingRequest) return;
    if (!canSubmitPromptDecision) return;
    const completed = completedPromptDecisionAnswers;
    if (!completed) return;
    setLocalError(null);
    setSubmittedDecisionSummary(buildDecisionSubmittedSummary(active.waitingRequest.groups, completed, decisionDrafts));
    setSubmittingDecision(true);
    try {
      const { refs: uploaded, drafts: uploadedDrafts } = await uploadDecisionDrafts(
        active.waitingRequest.groups,
        decisionDrafts,
      );
      const text = buildDecisionSupplementText(active.waitingRequest.groups, decisionAnswers, uploadedDrafts);
      const result = await resumePromptAssistant(active.generationId, {
        answers: completed,
        text,
        attachments: uploaded,
      }, active.controller.signal);
      const keepSession = handlePromptAssistantResponse(result, active.generationId);
      if (!keepSession) finishPromptAssistantGeneration(node.id, active.generationId);
    } catch (e) {
      setSubmittedDecisionSummary(null);
      if (!isAbortError(e) && mountedRef.current) showCaughtError(e, '提交回答失败', '提交失败');
    } finally {
      if (mountedRef.current) setSubmittingDecision(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col space-y-2">
      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={() => {
            if (!generating) setOpen((v) => !v);
          }}
          className="inline-flex items-center gap-1.5 rounded-full bg-[#F9F9F9] px-3 py-1.5 text-xs text-black/65 hover:bg-black/[0.04]"
          aria-label="生成提示词"
          title="生成提示词"
          disabled={generating}
        >
          <SparkleIcon className="w-4 h-4" />
          <span>生成提示词</span>
        </button>
      </div>
      {open && (
        <div className={`${showDecisionForm ? 'min-h-0 flex-1' : ''} flex flex-col space-y-2`}>
          <div className="flex gap-2 rounded-lg bg-[#F9F9F9] p-2">
            <input
              className="min-w-0 flex-1 bg-transparent px-2 text-sm outline-none"
              value={requestValue}
              placeholder="描述你想要的提示词"
              onChange={(e) => setRequest(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void generatePrompt();
              }}
              disabled={generating}
            />
            <button
              type="button"
              className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-white disabled:bg-black/30 ${
                generating ? 'bg-red-600 hover:bg-red-700' : 'bg-black hover:bg-black/85'
              }`}
              onClick={generating ? cancelGeneration : () => void generatePrompt()}
              aria-label={generating ? '中止' : '发送'}
            >
              {generating ? <StopIcon className="h-3.5 w-3.5" /> : <SendIcon className="w-4 h-4" />}
            </button>
          </div>
          {showDecisionForm && waitingRequest ? (
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
              <DecisionPromptPanel
                context={waitingRequest.context}
                groups={waitingRequest.groups}
                disabled={submittingDecision}
                autoComplete={false}
                autoAdvanceOnSingle={false}
                externallyCompletedGroupIds={completedDecisionGroupIdsValue}
                submittedSummary={submittedDecisionSummary}
                onAnswersChange={setDecisionAnswers}
                onActiveGroupChange={(groupId, index) => {
                  setActiveDecisionGroupId(groupId);
                  setActiveDecisionGroupIndex(index);
                }}
              />
              <PillInputBar
                value={activeDecisionText}
                onChange={(next) => updateActiveDecisionDraft({ text: next })}
                onSubmit={() => void submitPromptAssistantDecision()}
                placeholder="输入补充说明..."
                canSubmit={false}
                submitting={submittingDecision}
                ariaLabel="补充回答"
                allowAttachments={!!activeDecisionGroupId}
                attachments={activeDecisionAttachments}
                onAttachmentsChange={(next) => updateActiveDecisionDraft({ attachments: next })}
                readOnly={submittingDecision}
                hideSubmit
              />
              {activePromptDecisionIsLast ? (
                <button
                  type="button"
                  onClick={() => void submitPromptAssistantDecision()}
                  disabled={!canSubmitPromptDecision}
                  className="flex h-9 w-full items-center justify-center gap-2 rounded-full bg-black text-xs font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-35 disabled:hover:bg-black"
                >
                  提交回答
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      )}
      {error && <div className="text-xs text-red-600">{error}</div>}
      {showPromptTextarea ? (
        <div className="relative flex min-h-0 flex-1">
          <PromptTokenEditor
            ref={editorRef}
            className={mainTextareaCls}
            value={value}
            tokens={promptTokens}
            placeholder={promptPlaceholderForNode(node)}
            ariaLabel={`${nodeLabel}提示词`}
            onChange={(next) => {
              if (!generating) onChange(next, { skipSave: true });
            }}
            onBlur={flushPromptSave}
            readOnly={generating}
          />
          <LoadingOverlay show={generating} message="正在优化提示词，请稍候..." />
        </div>
      ) : null}
      {showPromptTextarea && !value.trim() && <div className="text-xs text-red-600">请填写提示词。</div>}
    </div>
  );
}

function promptPlaceholderForNode(node: GenerateNode | OutputNode | ConditionNode): string {
  if (node.type === 'generate') {
    return '写清当前节点基于上游输入要产出什么；不要写 {{node.output}}，上下文会自动传入。';
  }
  if (node.type === 'output') {
    return '写清最终页面要展示什么；HTML JSON 包装由系统处理，不用写 {"html":...}。';
  }
  return '写清判断依据和边界情况；输出需能匹配当前分支。';
}

function UrlField({
  urls,
  onChange,
  onTextChange,
}: {
  urls: string[];
  onChange: (urls: string[]) => void;
  onTextChange: (urls: string[]) => void;
}) {
  const visibleUrls = urls.map((url) => url.trim()).filter(Boolean);
  const updateText = onTextChange;
  const updateAt = (index: number, value: string) => {
    updateText(urls.map((url, i) => (i === index ? value : url)));
  };
  const normalize = () => {
    updateText(urls.map((url) => url.trim()).filter(Boolean));
  };
  const addUrl = () => {
    onChange([...urls, '']);
  };
  const removeAt = (index: number) => {
    onChange(urls.filter((_, i) => i !== index));
  };

  return (
    <div className="space-y-4">
      <Field label="URL">
        <div className="space-y-2">
          {urls.length > 0 ? (
            urls.map((url, index) => (
              <div key={index} className="flex items-center gap-2">
                <input
                  className="h-10 min-w-0 flex-1 rounded-lg border border-black/10 bg-white px-3 text-sm outline-none focus:border-black/30"
                  value={url}
                  placeholder="https://example.com/resource"
                  onChange={(event) => updateAt(index, event.target.value)}
                  onBlur={normalize}
                />
                <button
                  type="button"
                  onClick={() => removeAt(index)}
                  className="rounded-full p-2 text-black/45 hover:bg-black/5 hover:text-black/70"
                  aria-label="删除链接"
                  title="删除链接"
                >
                  <TrashIcon className="h-3.5 w-3.5" />
                </button>
              </div>
            ))
          ) : (
            <div className="rounded-lg bg-[#F9F9F9] p-4 text-sm text-black/45">尚未添加链接。</div>
          )}
          <button
            type="button"
            onClick={addUrl}
            className="inline-flex items-center gap-1.5 rounded-full bg-[#F9F9F9] px-3 py-1.5 text-sm hover:bg-black/[0.04]"
          >
            <PlusIcon className="h-3.5 w-3.5" />
            添加链接
          </button>
        </div>
      </Field>
      <section className="rounded-lg bg-[#F9F9F9] p-4">
        {visibleUrls.length ? (
          <ul className="space-y-2">
            {visibleUrls.map((url, index) => (
              <li key={`${url}-${index}`}>
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="break-all text-sm text-black underline decoration-black/25 underline-offset-4 hover:decoration-black"
                >
                  {url}
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-sm text-black/45">尚未填写链接。</div>
        )}
      </section>
    </div>
  );
}

interface UploadPreviewState {
  url: string;
  blob: Blob | null;
  loading: boolean;
  error: string | null;
}

function useUploadPreview(upload: UploadRef | null): UploadPreviewState {
  const [state, setState] = useState<UploadPreviewState>({ url: '', blob: null, loading: false, error: null });

  useEffect(() => {
    if (!upload) {
      setState({ url: '', blob: null, loading: false, error: null });
      return;
    }
    const controller = new AbortController();
    let objectUrl = '';
    setState({ url: '', blob: null, loading: true, error: null });
    fetchUploadBlob(upload.id, controller.signal)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setState({ url: objectUrl, blob, loading: false, error: null });
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : '素材加载失败';
        setState({ url: '', blob: null, loading: false, error: message });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [upload?.id]);

  return state;
}

function FileField({
  node,
  onChange,
}: {
  node: Extract<AssetNode, { asset_kind: 'file' }>;
  onChange: (uploads: UploadRef[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);

  const handleFiles = async (files: File[]) => {
    if (uploading) return;
    if (!files.length) return;
    const oversized = files.find((file) => file.size > MAX_FILE_BYTES);
    if (oversized) {
      setError(`文件「${oversized.name}」过大（${Math.ceil(oversized.size / 1024 / 1024)} MB > 10 MB）。`);
      return;
    }
    setError(null);
    setUploading(true);
    try {
      const uploaded: UploadRef[] = [];
      for (const file of files) {
        uploaded.push(await uploadFile(file));
      }
      onChange([...node.uploads, ...uploaded]);
    } catch (uploadError) {
      showCaughtError(uploadError, '上传失败', '上传失败');
    } finally {
      setUploading(false);
    }
  };

  const handleChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = '';
    void handleFiles(files);
  };

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (uploading) return;
    e.dataTransfer.dropEffect = 'copy';
    setDragging(true);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (uploading) return;
    void handleFiles(Array.from(e.dataTransfer.files ?? []));
  };

  return (
    <div className="space-y-4">
      <div className="block min-w-0" aria-label="文件">
        <div className="mb-1.5 text-[11px] uppercase tracking-wider text-black/45">文件</div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="image/*,video/*,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.md,.txt,.pdf,application/pdf,text/plain,text/markdown"
          onChange={handleChange}
          className="sr-only"
        />
        <div
          role="button"
          tabIndex={uploading ? -1 : 0}
          aria-label="添加文件"
          onClick={() => {
            if (!uploading) inputRef.current?.click();
          }}
          onKeyDown={(e) => {
            if (uploading) return;
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDragOver={handleDragOver}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          className={`grid min-h-[88px] w-full place-items-center rounded-lg border border-dashed transition ${
            dragging
              ? 'border-black/35 bg-black/[0.04]'
              : 'border-black/15 bg-[#F9F9F9] hover:border-black/25 hover:bg-black/[0.03]'
          } ${uploading ? 'pointer-events-none opacity-60' : 'cursor-pointer'}`}
        >
          {uploading ? (
            <span className="h-5 w-5 animate-spin rounded-full border-2 border-black/15 border-t-black/60" />
          ) : (
            <PlusIcon className="h-5 w-5 text-black/55" />
          )}
        </div>
        <div className="mt-1 text-[11px] text-black/45">
          {node.uploads.length ? `${node.uploads.length} 个文件` : '尚未选择文件'} · 单个最大 10 MB
        </div>
        {error && <div className="mt-1 text-[11px] text-red-600">{error}</div>}
      </div>
      {node.uploads.length > 0 && (
        <ul className="space-y-1.5">
          {node.uploads.map((upload, index) => (
            <li
              key={upload.id}
              className="flex items-center gap-2 rounded-lg bg-[#F9F9F9] px-3 py-2 text-sm"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-black/70">{upload.name}</div>
                <div className="mt-0.5 truncate font-mono text-[11px] text-black/45">
                  {upload.mime || 'application/octet-stream'} · {formatBytes(upload.size)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  const next = node.uploads.filter((_, i) => i !== index);
                  onChange(next);
                }}
                className="rounded-full p-1.5 text-black/45 hover:bg-black/5 hover:text-black/70"
                aria-label="删除文件"
                title="删除文件"
              >
                <TrashIcon className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DrawingField({
  upload,
  filename,
  onSave,
}: {
  upload: UploadRef | null;
  filename: string;
  onSave: (upload: UploadRef | null) => void;
}) {
  const ref = useRef<ReactSketchCanvasRef | null>(null);
  const canvasBoxRef = useRef<HTMLDivElement | null>(null);
  const preview = useUploadPreview(upload);
  const [baseImage, setBaseImage] = useState('');
  const [strokeColor, setStrokeColor] = useState(DRAWING_SWATCHES[0]);
  const [strokeWidth, setStrokeWidth] = useState(4);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setBaseImage(preview.url && (upload?.mime ?? '').startsWith('image/') ? preview.url : '');
    ref.current?.resetCanvas();
    setDirty(false);
  }, [preview.url, upload?.mime]);

  const handleClear = () => {
    setBaseImage('');
    ref.current?.resetCanvas();
    setDirty(true);
  };

  const handleSave = async () => {
    const paths = (await ref.current?.exportPaths()) ?? [];
    if (!baseImage && paths.length === 0) {
      onSave(null);
      setDirty(false);
      return;
    }
    const rect = canvasBoxRef.current?.getBoundingClientRect();
    setSaving(true);
    setError(null);
    try {
      const blob = await renderDrawingToBlob(paths, baseImage, rect?.width ?? 900, rect?.height ?? 560);
      const file = new File([blob], filename || 'drawing.png', { type: 'image/png' });
      const nextUpload = await uploadFile(file);
      onSave(nextUpload);
      ref.current?.resetCanvas();
      setDirty(false);
    } catch (saveError) {
      showCaughtError(saveError, '保存失败', '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3" aria-label="画板">
      <div className="relative shrink-0 pr-32">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="flex items-center gap-1 rounded-full bg-[#F9F9F9] p-1">
            {DRAWING_SWATCHES.map((color) => (
              <button
                key={color}
                type="button"
                aria-label={`画笔颜色 ${color}`}
                title={`画笔颜色 ${color}`}
                onClick={() => setStrokeColor(color)}
                className={`h-6 w-6 rounded-full border ${
                  strokeColor === color ? 'border-black shadow-[0_0_0_2px_rgba(0,0,0,0.12)]' : 'border-black/10'
                }`}
                style={{ backgroundColor: color }}
              />
            ))}
            <input
              type="color"
              value={strokeColor}
              aria-label="自定义画笔颜色"
              title="自定义画笔颜色"
              onChange={(e) => setStrokeColor(e.target.value)}
              className="h-6 w-6 cursor-pointer rounded-full border-0 bg-transparent p-0"
            />
          </div>
          <label className="flex items-center gap-2 rounded-full bg-[#F9F9F9] px-3 py-1.5 text-xs text-black/65">
            <span>画笔</span>
            <input
              type="range"
              min={1}
              max={18}
              value={strokeWidth}
              onChange={(e) => setStrokeWidth(Number(e.target.value))}
              className="w-24 accent-black"
            />
            <span className="w-5 text-right tabular-nums">{strokeWidth}</span>
          </label>
        </div>
        <div className="absolute right-0 top-0 flex items-center gap-2">
          {dirty && <span className="text-xs text-black/40">未保存</span>}
          <button
            type="button"
            onClick={handleClear}
            disabled={saving}
            className="rounded-full bg-[#F9F9F9] px-3 py-1.5 text-sm hover:bg-black/[0.04] disabled:opacity-40"
          >
            清空
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="rounded-full bg-black px-3 py-1.5 text-sm text-white hover:bg-black/85 disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
      {preview.error && <div className="text-xs text-red-600">{preview.error}</div>}
      {error && <div className="text-xs text-red-600">{error}</div>}
      <div ref={canvasBoxRef} className="min-h-[420px] flex-1 overflow-hidden rounded-lg border border-black/10 bg-white">
        <ReactSketchCanvas
          ref={ref}
          id="mira-drawing-canvas"
          className="mira-sketch-canvas"
          width="100%"
          height="100%"
          strokeColor={strokeColor}
          strokeWidth={strokeWidth}
          canvasColor="#fff"
          backgroundImage={baseImage}
          exportWithBackgroundImage
          preserveBackgroundImageAspectRatio="none"
          style={{ border: 0, borderRadius: 0 }}
          onStroke={() => setDirty(true)}
        />
      </div>
    </div>
  );
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

async function renderDrawingToBlob(paths: CanvasPath[], baseImage: string, width: number, height: number): Promise<Blob> {
  const canvas = document.createElement('canvas');
  const safeWidth = Math.max(1, Math.round(width));
  const safeHeight = Math.max(1, Math.round(height));
  canvas.width = safeWidth;
  canvas.height = safeHeight;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('无法创建画板');

  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, safeWidth, safeHeight);

  if (baseImage) {
    const img = await loadImage(baseImage);
    ctx.drawImage(img, 0, 0, safeWidth, safeHeight);
  }

  paths.forEach((path) => {
    if (!path.drawMode || path.paths.length === 0) return;
    ctx.strokeStyle = path.strokeColor;
    ctx.lineWidth = path.strokeWidth;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    path.paths.forEach((point, index) => {
      if (index === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.stroke();
  });

  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('无法导出画板');
  return blob;
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

function EditorShell({
  title,
  fallbackTitle,
  bg,
  onTitleChange,
  children,
}: {
  title: string;
  fallbackTitle: string;
  bg: string;
  onTitleChange: (title: string) => void;
  children: ReactNode;
}) {
  const displayTitle = title.trim() || fallbackTitle;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(displayTitle);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const skipBlurSaveRef = useRef(false);

  useEffect(() => {
    if (!editing) setDraft(displayTitle);
  }, [displayTitle, editing]);

  useEffect(() => {
    if (!editing) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [editing]);

  const startEditing = () => {
    setDraft(displayTitle);
    setEditing(true);
  };

  const saveDraft = () => {
    const nextTitle = draft.trim();
    setEditing(false);
    if (!nextTitle) {
      setDraft(displayTitle);
      return;
    }
    if (nextTitle !== title.trim() && (title.trim() || nextTitle !== fallbackTitle)) {
      onTitleChange(nextTitle);
    }
  };

  const cancelEditing = () => {
    skipBlurSaveRef.current = true;
    setDraft(displayTitle);
    setEditing(false);
    window.setTimeout(() => {
      skipBlurSaveRef.current = false;
    }, 0);
  };

  return (
    <div className="flex h-full flex-col text-sm">
      <div className={`${bg} px-4 py-3 text-sm font-semibold text-black/80`}>
        {editing ? (
          <input
            ref={inputRef}
            className="h-6 w-full min-w-0 rounded bg-white/65 px-2 text-sm font-semibold text-black/80 outline-none ring-1 ring-black/10 focus:ring-black/25"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              if (skipBlurSaveRef.current) {
                skipBlurSaveRef.current = false;
                return;
              }
              saveDraft();
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                saveDraft();
              }
              if (e.key === 'Escape') {
                e.preventDefault();
                cancelEditing();
              }
            }}
            aria-label="节点标题"
          />
        ) : (
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              onClick={startEditing}
              className="min-w-0 flex-1 truncate text-left hover:text-black"
              aria-label="编辑标题"
              title={displayTitle}
            >
              {displayTitle}
            </button>
            <button
              type="button"
              onClick={startEditing}
              className="shrink-0 rounded-full p-1 text-black/45 hover:bg-black/5 hover:text-black/70"
              aria-label="编辑标题"
              title="编辑标题"
            >
              <EditIcon className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden p-4">
        {children}
      </div>
    </div>
  );
}

function Field({ label, children, className = '' }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block min-w-0 ${className}`} aria-label={label}>
      <div className="mb-1.5 text-[11px] uppercase tracking-wider text-black/45">{label}</div>
      {children}
    </label>
  );
}

const selectButtonCls = 'flex h-9 w-44 max-w-full items-center rounded-full bg-[#F9F9F9] px-3 text-sm outline-none hover:bg-black/[0.04]';
const mainTextareaCls = 'min-h-0 flex-1 resize-none overflow-y-auto border-0 bg-transparent p-0 text-sm leading-relaxed outline-none placeholder:text-black/35';
