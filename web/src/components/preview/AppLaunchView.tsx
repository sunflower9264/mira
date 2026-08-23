import { useEffect, useMemo, useState } from 'react';
import { PlayIcon, SendIcon } from '../common/Icons';
import { PillInputBar, type PillAttachment } from '../common/PillInputBar';
import { AppAgentSelect } from '../common/AppAgentSelect';
import { AppToolsInlineSelect } from '../common/AppToolsInlineSelect';
import { WorkflowLintNotice } from '../common/WorkflowLintNotice';
import { isAgentEnabled } from '../../lib/agentOptions';
import { useSettingsStore } from '../../stores/useSettingsStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import * as api from '../../lib/api';
import type { App, AppAgentKind, ConditionNode, GenerateNode, OutputNode, UserInputNode, WorkflowLintResult, WorkflowNode } from '../../types';
import { useAppCoverUrl } from '../../hooks/useAppCoverUrl';

type Density = 'compact' | 'spacious';

/**
 * 单个 user_input 节点的提交值。简单字符串与带附件的 RunInputValue 形态都支持，
 * 后端 normalize_run_inputs 会统一处理。
 */
interface LaunchInputValue {
  value: string;
  attachments?: { id: string; name?: string }[];
}

export type LaunchInputs = Record<string, string | LaunchInputValue>;

interface AppLaunchViewProps {
  app: App;
  onStart(inputs: LaunchInputs): void | Promise<void>;
  onAgentChange?(agent: AppAgentKind, supportedModels: string[]): void;
  onToolsChange?(disabledToolIds: string[]): void;
  density?: Density;
  error?: string | null;
}

const tokens = {
  compact: {
    page: 'px-6 pt-8 pb-4',
    grid: 'gap-5',
    hero: 'gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]',
    cover: 'aspect-video rounded-3xl',
    coverPlaceholder: 'aspect-video rounded-3xl',
    title: 'text-2xl',
    description: 'text-sm leading-6 line-clamp-2',
    metaText: 'text-xs',
    startButton: 'h-11 px-5 text-sm',
    footerPad: 'px-4 pb-[31px] pt-2',
  },
  spacious: {
    page: 'px-8 pt-12 pb-4',
    grid: 'gap-8',
    hero: 'gap-8 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]',
    cover: 'aspect-video rounded-3xl',
    coverPlaceholder: 'aspect-video rounded-3xl',
    title: 'text-4xl',
    description: 'text-base leading-7 line-clamp-3',
    metaText: 'text-sm',
    startButton: 'h-12 px-6 text-base',
    footerPad: 'px-6 pb-[31px] pt-3',
  },
} as const;

function CoverImage({ src, classes }: { src: string; classes: string }) {
  return (
    <div
      className={`${classes} w-full bg-cover bg-center shadow-card`}
      style={{ backgroundImage: `url(${src})` }}
      aria-hidden
    />
  );
}

function CoverPlaceholder({ classes }: { classes: string }) {
  return (
    <div className={`${classes} w-full border border-dashed border-black/15 bg-black/[0.02] flex items-center justify-center text-xs text-black/40`}>
      封面占位
    </div>
  );
}

const noEnabledAgentMessage = '无可用 Agent，请先在设置中启用 Agent';

type PromptNode = GenerateNode | ConditionNode | OutputNode;

function isPromptNode(node: WorkflowNode): node is PromptNode {
  return node.type === 'generate' || node.type === 'condition' || node.type === 'output';
}

function getInputPlaceholder(node: UserInputNode) {
  return node.input_schema.placeholder ?? node.input_schema.label ?? node.title ?? '输入内容…';
}

const SCROLLBAR_CLASSES =
  '[&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-button]:hidden [&::-webkit-scrollbar-button]:h-0 [&::-webkit-scrollbar-button]:w-0 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-black/20';

export function AppLaunchView({ app, onStart, onAgentChange, onToolsChange, density = 'compact', error = null }: AppLaunchViewProps) {
  const t = tokens[density];
  const coverUrl = useAppCoverUrl(app);
  const settings = useSettingsStore((s) => s.settings);
  const loadSettings = useSettingsStore((s) => s.load);
  const sourceHidden = !app.can_view_source;
  const promptNodes = useMemo(
    () => sourceHidden ? [] : app.graph.nodes.filter(isPromptNode),
    [app.graph.nodes, sourceHidden],
  );
  const userInputs = useMemo(
    () => app.graph.nodes.filter((n): n is UserInputNode => n.type === 'user_input'),
    [app.graph.nodes],
  );
  const outputs = useMemo(
    () => app.graph.nodes.filter((n): n is OutputNode => n.type === 'output'),
    [app.graph.nodes],
  );
  const totalNodes = app.graph.nodes.length;
  const empty = totalNodes === 0;

  // 同时只有一个活跃 user_input 节点；只要存在就作为启动前输入收集。
  const activeUserInput = userInputs[0] ?? null;

  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<PillAttachment[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [lintResult, setLintResult] = useState<WorkflowLintResult | null>(null);
  const [lintLoading, setLintLoading] = useState(false);
  const [lintError, setLintError] = useState<string | null>(null);

  useEffect(() => {
    if (!settings) void loadSettings().catch(() => undefined);
  }, [settings, loadSettings]);

  useEffect(() => {
    const controller = new AbortController();
    setLintLoading(true);
    setLintError(null);
    void api.lintAppGraph(app.id, app.can_view_source ? app.graph : undefined, controller.signal)
      .then((result) => {
        setLintResult(result);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        setLintError(err instanceof Error ? err.message : '预检失败');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLintLoading(false);
      });
    return () => controller.abort();
  }, [app.can_view_source, app.id, app.graph]);

  const enabledAgentRuntimes = useMemo(
    () => new Set((settings?.agents ?? []).filter((agent) => agent.enabled).map((agent) => agent.runtime)),
    [settings?.agents],
  );
  const hasEnabledAgent = enabledAgentRuntimes.size > 0;
  const settingsReady = settings !== null;
  const appAgent = app.graph.agent ?? '';
  const hasSelectedAgent = useMemo(() => isAgentEnabled(settings, appAgent), [settings, appAgent]);
  const invalidAgentMessages = useMemo(() => {
    if (sourceHidden) return [];
    if (!settingsReady) return [];
    const messages = promptNodes.flatMap((node) =>
      node.prompt?.trim() ? [] : [`${node.title || node.id}：未填写提示词`],
    );
    if (promptNodes.length > 0 && !hasSelectedAgent) {
      messages.push(appAgent ? `应用默认 Agent「${appAgent}」未启用` : '应用未选择 Agent');
    }
    return messages;
  }, [appAgent, hasSelectedAgent, promptNodes, settingsReady, sourceHidden]);

  const setValue = (id: string, value: string) =>
    setInputValues((current) => ({ ...current, [id]: value }));

  const activeFilled = activeUserInput
    ? (inputValues[activeUserInput.id] ?? '').trim().length > 0
    : true;
  const agentReady = sourceHidden || promptNodes.length === 0 || (settingsReady && hasSelectedAgent);
  const hasLintErrors = (lintResult?.summary.errors ?? 0) > 0;
  const canStart = app.can_run && !empty && activeFilled && !submitting && agentReady && invalidAgentMessages.length === 0 && !hasLintErrors;

  const [uploadError, setUploadError] = useState<string | null>(null);

  const handleStart = async () => {
    if (!canStart) return;
    setSubmitting(true);
    setUploadError(null);
    try {
      const inputs: LaunchInputs = { ...inputValues };
      // 把附件先调 POST /api/uploads 拿到 upload id；然后用 RunInputValue 形态。
      if (attachments.length > 0 && activeUserInput) {
        const uploadedRefs: { id: string; name?: string }[] = [];
        const next = [...attachments];
        for (let i = 0; i < next.length; i += 1) {
          const item = next[i];
          if (item.uploadId) {
            uploadedRefs.push({ id: item.uploadId, name: item.name });
            continue;
          }
          if (!item.file) {
            setUploadError(`附件「${item.name}」缺少文件内容，无法上传`);
            return;
          }
          const result = await api.uploadFile(item.file);
          next[i] = { ...item, uploadId: result.id };
          uploadedRefs.push({ id: result.id, name: item.name });
        }
        setAttachments(next);
        const value = inputs[activeUserInput.id] ?? '';
        const text = typeof value === 'string' ? value : value.value;
        inputs[activeUserInput.id] = { value: text, attachments: uploadedRefs };
      }
      await onStart(inputs);
    } catch (error) {
      showCaughtError(error, '启动运行失败', '启动失败');
    } finally {
      setSubmitting(false);
    }
  };

  const meta = sourceHidden
    ? '仅运行应用'
    : [
        `${totalNodes} 个节点`,
        `${userInputs.length} 项输入`,
        `${outputs.length} 项产出`,
      ].join(' · ');

  const renderHero = (
    <section className={`grid ${t.hero}`}>
      <div>
        {coverUrl ? <CoverImage src={coverUrl} classes={t.cover} /> : <CoverPlaceholder classes={t.coverPlaceholder} />}
      </div>
      <div className="flex min-w-0 flex-col justify-center">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className={`${t.title} font-semibold tracking-tight text-black`}>{app.name}</h2>
          <span className="rounded-full border border-black/10 px-2 py-0.5 text-[10px] uppercase tracking-wider text-black/55">
            {app.status === 'published' ? '已发布' : '草稿'}
          </span>
        </div>
        <p className={`mt-3 ${t.description} text-black/60`}>
          {app.description || '运行这个应用即可开始。'}
        </p>
      </div>
    </section>
  );

  const renderMeta = (
    <section className="space-y-1">
      {onAgentChange && (
        <AppAgentSelect
          value={appAgent}
          onChange={onAgentChange}
          label="默认 Agent"
          className="mb-2"
        />
      )}
      {onToolsChange && (
        <AppToolsInlineSelect
          disabledToolIds={app.graph.tools?.disabled_tool_ids ?? []}
          onChange={onToolsChange}
          className="mb-2"
        />
      )}
      <div className={`${t.metaText} text-black/55`}>{meta}</div>
      {!app.can_run && (
        <div className="text-xs text-black/55">应用已下架，只能查看历史运行记录。</div>
      )}
      {settingsReady && !hasEnabledAgent && (
        <div className="text-xs text-amber-700">{noEnabledAgentMessage}。</div>
      )}
      {settingsReady && hasEnabledAgent && invalidAgentMessages.length > 0 && (
        <div className="space-y-0.5 text-xs text-amber-700">
          {invalidAgentMessages.slice(0, 3).map((message) => (
            <div key={message}>{message}</div>
          ))}
        </div>
      )}
      <WorkflowLintNotice result={lintResult} loading={lintLoading} error={lintError} />
    </section>
  );

  const renderFileInput = (node: UserInputNode) => {
    return (
      <input
        type="file"
        onChange={(event) => setValue(node.id, event.target.value)}
        className="block w-full text-sm text-black/70 file:mr-3 file:rounded-full file:border-0 file:bg-black file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-white hover:file:bg-black/85"
      />
    );
  };

  const renderFooter = (
    <div className={`shrink-0 ${t.footerPad}`}>
      <div className="mx-auto w-full max-w-[560px] px-4">
        {!activeUserInput ? (
          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => void handleStart()}
              disabled={!canStart}
              className={`inline-flex items-center gap-2 rounded-full bg-black ${t.startButton} font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50`}
            >
              <PlayIcon className="h-4 w-4" />
              {submitting ? '启动中…' : '开始'}
            </button>
          </div>
        ) : activeUserInput.input_schema.kind !== 'file' ? (
          <PillInputBar
            value={inputValues[activeUserInput.id] ?? ''}
            onChange={(v) => setValue(activeUserInput.id, v)}
            onSubmit={() => void handleStart()}
            placeholder={getInputPlaceholder(activeUserInput)}
            canSubmit={app.can_run && (canStart || attachments.length > 0)}
            submitting={submitting}
            allowAttachments
            attachments={attachments}
            onAttachmentsChange={setAttachments}
          />
        ) : (
          <div className="flex items-center gap-2 bg-white border border-black/10 rounded-full shadow-pill px-4 py-2">
            {renderFileInput(activeUserInput)}
            <button
              type="button"
              aria-label="发送"
              onClick={() => void handleStart()}
              disabled={!canStart}
              className="p-1.5 rounded-full bg-black text-white disabled:bg-black/30 shrink-0"
            >
              {submitting ? (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              ) : (
                <SendIcon className="w-4 h-4" />
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <div className="flex h-full min-h-full flex-col">
      <div className={`flex-1 overflow-y-auto ${SCROLLBAR_CLASSES}`}>
        <div className={`${t.page}`}>
          <div className={`mx-auto flex w-full max-w-3xl flex-col ${t.grid}`}>
            {renderHero}
            {renderMeta}
            {empty && (
              <div className="rounded-2xl border border-dashed border-black/15 bg-black/[0.02] px-3 py-10 text-center text-sm text-black/45">
                画布暂无节点，添加用户输入节点、生成节点、输出节点后即可启动。
              </div>
            )}
            {(error || uploadError) && (
              <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                {uploadError || error}
              </div>
            )}
          </div>
        </div>
      </div>
      {renderFooter}
    </div>
  );
}
