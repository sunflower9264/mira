// Bottom natural-language input bar. It calls /api/nlcompile, then shows the
// returned plan in a confirmation dialog before asking the backend to generate
// and apply graph patches to the canvas.

import { useCallback, useEffect, useRef, useState } from 'react';
import { hasPendingAttachments, hasPendingDraftAttachments, patchDraftAttachment, PillInputBar, type PillAttachment } from '../common/PillInputBar';
import { AppDialog } from '../common/AppDialog';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { completeDecisionAnswers, DecisionPromptPanel } from '../common/DecisionPromptPanel';
import { StopIcon } from '../common/Icons';
import { AiActivityIndicator } from '../common/AiActivityIndicator';
import {
  buildDecisionSubmittedSummary,
  buildDecisionSupplementText,
  completedDecisionGroupIds,
  type DecisionSubmittedSummary,
  type DecisionSupplementDrafts,
} from '../common/decisionInput';
import { useEditorStore } from '../../stores/useEditorStore';
import {
  applyNlCompile,
  cancelNlCompile,
  getActiveNlCompile,
  nlCompile,
  refineNlCompile,
  resumeNlCompile,
  uploadFile,
  type NlCompileResponse,
} from '../../lib/api';
import { uuid } from '../../lib/utils';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import type { DecisionAnswer, NlCompilePlan } from '../../types';

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError';
}

interface PendingPlan {
  instruction: string;
  compile_id: string;
  plan: NlCompilePlan;
}

interface PendingCompileDecision {
  instruction: string;
  compile_id: string;
  request: Extract<NlCompileResponse, { status: 'waiting_for_user' }>['request'];
}

interface ConversationMessage {
  id: string;
  role: 'user' | 'assistant';
  text?: string;
  plan?: NlCompilePlan;
  decision?: PendingCompileDecision['request'];
  error?: boolean;
}

interface ActiveCompile {
  id: string;
  controller: AbortController;
  kind: 'initial' | 'resume' | 'regenerate' | 'apply' | 'restore';
  instruction: string;
  settled?: boolean;
  completed?: boolean;
}

function PlanSection({ title, items }: { title: string; items: string[] }) {
  const values = items.filter((item) => item.trim());
  return (
    <section>
      <h3 className="mb-1 text-xs font-semibold text-black/45">{title}</h3>
      {values.length ? (
        <ul className="space-y-1">
          {values.map((item, index) => (
            <li key={`${title}-${index}`} className="rounded-lg bg-black/[0.03] px-3 py-2">
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded-lg bg-black/[0.03] px-3 py-2 text-black/45">无</div>
      )}
    </section>
  );
}

function compactPlanChanges(plan: NlCompilePlan): string[] {
  const changes = plan.graph_changes.filter((item) => item.trim());
  if (changes.length > 0) return changes;
  const steps = plan.implementation_steps.filter((item) => item.trim());
  if (steps.length > 0) return steps;
  return ['没有要应用的更改'];
}

export function NlInputBar({ empty, conversation = false }: { empty: boolean; conversation?: boolean }) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [planMessageId, setPlanMessageId] = useState('');
  const [decisionMessageId, setDecisionMessageId] = useState('');
  const conversationRef = useRef<HTMLDivElement>(null);
  const followMessagesRef = useRef(true);
  const [value, setValue] = useState('');
  const [attachments, setAttachments] = useState<PillAttachment[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const cancelInFlightRef = useRef(false);
  const [activeCompile, setActiveCompile] = useState<ActiveCompile | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingPlan | null>(null);
  const [waitingCompile, setWaitingCompile] = useState<PendingCompileDecision | null>(null);
  const [decisionAnswers, setDecisionAnswers] = useState<DecisionAnswer[]>([]);
  const [activeDecisionGroupId, setActiveDecisionGroupId] = useState('');
  const [activeDecisionGroupIndex, setActiveDecisionGroupIndex] = useState(0);
  const [decisionDrafts, setDecisionDrafts] = useState<DecisionSupplementDrafts>({});
  const [submittedDecisionSummary, setSubmittedDecisionSummary] = useState<DecisionSubmittedSummary | null>(null);
  const [cancelConfirmOpen, setCancelConfirmOpen] = useState(false);
  // 弹窗内“补充修改说明”输入框的值，独立于底部 PillInputBar。
  const [supplement, setSupplement] = useState('');
  const activeCompileRef = useRef<ActiveCompile | null>(null);
  const app = useEditorStore((s) => s.app);
  const setGraph = useEditorStore((s) => s.setGraph);
  const waitingGroups = waitingCompile?.request.groups ?? [];
  const completedDecisionGroupIdsValue = waitingCompile
    ? completedDecisionGroupIds(waitingGroups, decisionAnswers)
    : [];
  const allDecisionGroupsComplete = waitingCompile
    ? waitingGroups.length > 0 && completedDecisionGroupIdsValue.length === waitingGroups.length
    : false;
  const completedDecisionAnswers = waitingCompile
    ? completeDecisionAnswers(waitingCompile.request.groups, decisionAnswers)
    : null;
  const activeDecisionDraft = activeDecisionGroupId ? decisionDrafts[activeDecisionGroupId] : undefined;
  const activeDecisionText = activeDecisionDraft?.text ?? '';
  const activeDecisionAttachments = activeDecisionDraft?.attachments ?? [];
  const activeAllowsText = !!waitingCompile && !!activeDecisionGroupId;
  const activeAllowsFiles = !!waitingCompile && !!activeDecisionGroupId;
  const activeIsLastDecisionGroup = waitingGroups.length > 0 && activeDecisionGroupIndex >= waitingGroups.length - 1;
  const activeCompileKind = activeCompile?.kind;
  const applyingPlan = activeCompileKind === 'apply';
  const regeneratingPlan = activeCompileKind === 'regenerate';
  const resumingDecision = activeCompileKind === 'resume';
  const canSubmit = !restoring && (waitingCompile
    ? allDecisionGroupsComplete && activeIsLastDecisionGroup && !submitting && !pending && !hasPendingDraftAttachments(decisionDrafts)
    : !!app && (!!value.trim() || attachments.length > 0) && !submitting && !pending && !hasPendingAttachments(attachments));
  const placeholder = waitingCompile
    ? '输入补充说明'
    : empty
      ? '描述你想搭建什么'
      : '编辑这些步骤';

  const recordMessage = (message: Omit<ConversationMessage, 'id'>) => {
    const id = uuid();
    if (conversation) setMessages((current) => [...current, { ...message, id }]);
    return id;
  };

  const reportError = (error: unknown, fallback: string, title = '自然语言编辑失败') => {
    if (conversation) recordMessage({ role: 'assistant', text: error instanceof Error ? error.message : typeof error === 'string' ? error : fallback, error: true });
    else showCaughtError(typeof error === 'string' ? new Error(error) : error, fallback, title);
  };

  useEffect(() => {
    const container = conversationRef.current;
    if (container && followMessagesRef.current) container.scrollTop = container.scrollHeight;
  }, [messages, activeCompile, pending, waitingCompile, submittedDecisionSummary]);

  const showToast = (message: string) => {
    if (conversation) return;
    setToast(message);
    window.setTimeout(() => setToast(null), 2500);
  };

  const beginActiveCompile = (active: ActiveCompile) => {
    activeCompileRef.current = active;
    setActiveCompile(active);
    setSubmitting(true);
  };

  const finishActiveCompile = (compileId: string) => {
    if (activeCompileRef.current?.id !== compileId) return;
    activeCompileRef.current.settled = true;
    if (cancelInFlightRef.current) return;
    activeCompileRef.current = null;
    setActiveCompile(null);
    setSubmitting(cancelInFlightRef.current);
  };

  const clearDecisionState = () => {
    setDecisionAnswers([]);
    setActiveDecisionGroupId('');
    setActiveDecisionGroupIndex(0);
    setDecisionDrafts({});
    setSubmittedDecisionSummary(null);
  };

  const updateActiveDecisionDraft = (
    patch: Partial<{ text: string; attachments: PillAttachment[] }>,
  ) => {
    if (!activeDecisionGroupId) return;
    setDecisionDrafts((current) => {
      const previous = current[activeDecisionGroupId] ?? { text: '', attachments: [] };
      return {
        ...current,
        [activeDecisionGroupId]: { ...previous, ...patch },
      };
    });
  };

  const handleDecisionAnswersChange = (answers: DecisionAnswer[]) => {
    setDecisionAnswers(answers);
  };

  const handleActiveDecisionGroupChange = useCallback((groupId: string, index: number) => {
    setActiveDecisionGroupId(groupId);
    setActiveDecisionGroupIndex(index);
  }, []);

  const cancelActiveCompile = async () => {
    const active = activeCompileRef.current;
    if (!active || cancelInFlightRef.current) return;
    cancelInFlightRef.current = true;
    setSubmitting(true);
    setCancelConfirmOpen(false);
    try {
      await cancelNlCompile(active.id);
      if (activeCompileRef.current !== active) return;
      activeCompileRef.current = null;
      active.controller.abort();
      setActiveCompile(null);
      setSubmitting(false);
      setPending(null);
      setWaitingCompile(null);
      clearDecisionState();
      setSupplement('');
      if (!active.completed) {
        setValue(active.instruction);
        recordMessage({ role: 'assistant', text: '已停止本次编辑。' });
      }
    } catch (error) {
      reportError(error, '停止自然语言编辑失败', '停止失败');
    } finally {
      cancelInFlightRef.current = false;
      if (activeCompileRef.current === active && active.settled) finishActiveCompile(active.id);
      if (!activeCompileRef.current) setSubmitting(cancelInFlightRef.current);
    }
  };

  const submittedSummaryAction = submittedDecisionSummary && resumingDecision ? (
    <button
      type="button"
      onClick={cancelActiveCompile}
      className="inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-full bg-red-600 px-3 text-xs font-medium text-white transition hover:bg-red-700"
      aria-label="终止"
      title="终止"
    >
      <StopIcon className="h-3.5 w-3.5" />
      终止
    </button>
  ) : null;

  const handleCompileResponse = (response: NlCompileResponse, instruction: string) => {
    if (response.status === 'planning' || response.status === 'applying') {
      showToast(response.status === 'applying' ? '正在生成画布，请稍候' : '自然语言编辑仍在处理中');
      return;
    }
    if (response.status === 'interrupted') {
      if (response.request) {
        setDecisionMessageId(recordMessage({ role: 'assistant', decision: response.request }));
        setPending(null);
        setWaitingCompile({
          instruction: response.instruction || instruction,
          compile_id: response.compile_id,
          request: response.request,
        });
        setActiveDecisionGroupId(response.request.groups[0]?.id ?? '');
        setActiveDecisionGroupIndex(0);
        setDecisionAnswers([]);
        setDecisionDrafts({});
        setSubmittedDecisionSummary(null);
        reportError(response.error || '自然语言编辑已暂停，请继续回答', '自然语言编辑已暂停');
        return;
      }
      if (response.plan) {
        setPlanMessageId(recordMessage({ role: 'assistant', plan: response.plan }));
        setPending({
          instruction: response.instruction || instruction,
          compile_id: response.compile_id,
          plan: response.plan,
        });
        setWaitingCompile(null);
        clearDecisionState();
        reportError(response.error || '自然语言编辑已暂停，可重新确认方案', '自然语言编辑已暂停');
        return;
      }
      setPending(null);
      setWaitingCompile(null);
      clearDecisionState();
      setValue(response.instruction || instruction);
      reportError(response.error || '自然语言编辑已暂停，请重新提交继续', '自然语言编辑已暂停');
      return;
    }
    if (response.status === 'waiting_for_user') {
      setDecisionMessageId(recordMessage({ role: 'assistant', decision: response.request }));
      setPending(null);
      setWaitingCompile({
        instruction,
        compile_id: response.compile_id,
        request: response.request,
      });
      setActiveDecisionGroupId(response.request.groups[0]?.id ?? '');
      setActiveDecisionGroupIndex(0);
      setDecisionAnswers([]);
      setDecisionDrafts({});
      setSubmittedDecisionSummary(null);
      return;
    }
    if (response.status === 'planned') {
      const { compile_id, plan } = response;
      setPlanMessageId(recordMessage({ role: 'assistant', plan }));
      setPending({ instruction, compile_id, plan });
      setWaitingCompile(null);
      clearDecisionState();
      return;
    }
    if (response.status === 'completed') {
      if (activeCompileRef.current) activeCompileRef.current.completed = true;
      setPending(null);
      setWaitingCompile(null);
      clearDecisionState();
      setSupplement('');
      setGraph(response.new_graph);
      recordMessage({ role: 'assistant', text: [response.applied_patches.length ? `已应用 ${response.applied_patches.length} 项更改。` : '本次没有需要应用的更改。', ...(response.warnings ?? [])].join('\n') });
      const count = response.applied_patches.length;
      showToast(response.warnings?.[0] ?? (count ? `已更新 ${count} 个步骤` : '没有应用任何更改'));
    }
  };

  const pollActiveCompile = async (
    appId: string,
    compileId: string,
    instruction: string,
    signal: AbortSignal,
  ): Promise<boolean> => {
    while (!signal.aborted) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      if (signal.aborted) return false;
      const response = await getActiveNlCompile(appId, signal);
      if (signal.aborted) return false;
      if (response?.status === 'completed') {
        handleCompileResponse(response, instruction);
        return true;
      }
      if (!response || response.compile_id !== compileId) {
        setPending(null);
        setWaitingCompile(null);
        clearDecisionState();
        setValue(instruction);
        reportError('编辑任务已结束，但未收到可应用的结果。请重新提交需求。', '编辑结果未收到');
        return false;
      }
      if (response.status === 'planning' || response.status === 'applying') continue;
      handleCompileResponse(response, 'instruction' in response && response.instruction ? response.instruction : instruction);
      return response.status !== 'interrupted';
    }
    return false;
  };

  useEffect(() => {
    if (!app?.id) return;
    setRestoring(true);
    setSubmitting(false);
    setActiveCompile(null);
    setPending(null);
    setWaitingCompile(null);
    clearDecisionState();
    setValue('');
    setAttachments([]);
    setMessages([]);
    setPlanMessageId('');
    setDecisionMessageId('');
    followMessagesRef.current = true;
    const controller = new AbortController();
    getActiveNlCompile(app.id, controller.signal)
      .then((response) => {
        if (!response || controller.signal.aborted) return;
        if (response.status === 'completed') return;
        const instruction = 'instruction' in response && response.instruction ? response.instruction : value;
        if (instruction) recordMessage({ role: 'user', text: instruction });
        if (response.status === 'planning' || response.status === 'applying') {
          beginActiveCompile({ id: response.compile_id, controller, kind: 'restore', instruction });
          void pollActiveCompile(app.id, response.compile_id, instruction, controller.signal)
            .catch((error) => { if (!isAbortError(error) && !cancelInFlightRef.current) reportError(error, '自然语言编辑恢复失败'); })
            .finally(() => { finishActiveCompile(response.compile_id); });
          return;
        }
        handleCompileResponse(response, instruction);
      })
      .catch((error) => {
        if (!isAbortError(error) && !cancelInFlightRef.current) reportError(error, '自然语言编辑恢复失败', '自然语言编辑失败');
      })
      .finally(() => { if (!controller.signal.aborted) setRestoring(false); });
    return () => {
      controller.abort();
      activeCompileRef.current?.controller.abort();
      activeCompileRef.current = null;
    };
  }, [app?.id]);

  // 通用：基于指令调用一次 /api/nlcompile，结果写入 pending 或 waitingCompile。
  const runCompile = async (
    instruction: string,
    kind: ActiveCompile['kind'] = 'initial',
    uploaded: { id: string; name?: string }[] = [],
  ): Promise<boolean> => {
    if (!app || useEditorStore.getState().app?.id !== app.id || !instruction.trim()) return false;
    const compileId = `nlc_${uuid()}`;
    const controller = new AbortController();
    beginActiveCompile({ id: compileId, controller, kind, instruction });
    try {
      const response = await nlCompile({
        app_id: app.id,
        compile_id: compileId,
        instruction,
        current_graph: app.graph,
        attachments: uploaded,
      }, controller.signal);
      if (activeCompileRef.current?.id !== compileId) return false;
      if (response.status === 'planning' || response.status === 'applying') {
        return await pollActiveCompile(app.id, compileId, instruction, controller.signal);
      }
      handleCompileResponse(response, instruction);
      return response.status !== 'interrupted';
    } catch (error) {
      if (!isAbortError(error) && !cancelInFlightRef.current) reportError(error, '自然语言编辑失败', '自然语言编辑失败');
      return false;
    } finally {
      finishActiveCompile(compileId);
    }
  };

  const uploadPending = async (
    source: PillAttachment[] = attachments,
  ): Promise<{ id: string; name?: string }[]> => {
    const refs: { id: string; name?: string }[] = [];
    const next = [...source];
    for (let i = 0; i < next.length; i += 1) {
      const item = next[i];
      if (item.uploadId) {
        refs.push({ id: item.uploadId, name: item.name });
        continue;
      }
      if (!item.file) throw new Error(`附件「${item.name}」缺少文件内容`);
      const result = await uploadFile(item.file);
      next[i] = { ...item, uploadId: result.id };
      refs.push({ id: result.id, name: item.name });
    }
    if (useEditorStore.getState().app?.id === app?.id) setAttachments(next);
    return refs;
  };

  const uploadDecisionDrafts = async (
    source: DecisionSupplementDrafts,
  ): Promise<{ refs: { id: string; name?: string }[]; drafts: DecisionSupplementDrafts }> => {
    const refs: { id: string; name?: string }[] = [];
    const next: DecisionSupplementDrafts = { ...source };
    for (const group of waitingGroups) {
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
    if (useEditorStore.getState().app?.id === app?.id) setDecisionDrafts(next);
    return { refs, drafts: next };
  };

  const resumeCompile = async (
    answers: DecisionAnswer[],
    text?: string,
    uploaded?: { id: string; name?: string }[],
  ) => {
    if (!waitingCompile || Boolean(activeCompileRef.current) || useEditorStore.getState().app?.id !== app?.id) return false;
    const compileId = waitingCompile.compile_id;
    const instruction = waitingCompile.instruction;
    const controller = new AbortController();
    beginActiveCompile({ id: compileId, controller, kind: 'resume', instruction });
    try {
      const response = await resumeNlCompile(compileId, {
        answers,
        text: text || null,
        attachments: uploaded ?? [],
      }, controller.signal);
      if (activeCompileRef.current?.id !== compileId) return false;
      if (app && (response.status === 'planning' || response.status === 'applying')) {
        return await pollActiveCompile(app.id, compileId, instruction, controller.signal);
      }
      handleCompileResponse(response, instruction);
      return response.status !== 'interrupted';
    } catch (error) {
      if (!isAbortError(error) && !cancelInFlightRef.current) reportError(error, '提交回答失败', '提交失败');
      return false;
    } finally {
      finishActiveCompile(compileId);
    }
  };

  const submitCompileInput = async () => {
    if (!waitingCompile || submitting) return;
    if (!canSubmit) return;
    const previousDrafts = decisionDrafts;
    const answers = completedDecisionAnswers ?? [];
    followMessagesRef.current = true;
    recordMessage({ role: 'user', text: waitingGroups.map((group) => {
      const selected = answers.find((answer) => answer.group_id === group.id)?.selected ?? [];
      const draft = previousDrafts[group.id];
      return [`${group.label}：${selected.join('、')}`, draft?.text, draft?.attachments.length ? `附件：${draft.attachments.map((item) => item.name).join('、')}` : ''].filter(Boolean).join('\n');
    }).join('\n\n') });
    setSubmittedDecisionSummary(buildDecisionSubmittedSummary(waitingGroups, answers, previousDrafts));
    setSubmitting(true);
    try {
      const { refs: uploaded, drafts: uploadedDrafts } = await uploadDecisionDrafts(previousDrafts);
      const text = buildDecisionSupplementText(
        waitingGroups,
        decisionAnswers,
        uploadedDrafts,
      );
      const ok = await resumeCompile(
        answers,
        text,
        uploaded,
      );
      if (!ok) setSubmittedDecisionSummary(null);
    } catch (error) {
      setSubmittedDecisionSummary(null);
      reportError(error, '提交回答失败', '提交失败');
    } finally {
      if (!activeCompileRef.current) setSubmitting(false);
    }
  };

  const submit = async () => {
    if (waitingCompile) {
      await submitCompileInput();
      return;
    }
    if (!app || restoring || submitting || pending) return;
    if (!value.trim() && attachments.length === 0) return;
    const attachmentNote =
      attachments.length > 0 ? `\n\n附件: ${attachments.map((a) => a.name).join('、')}` : '';
    const instruction = `${value}${attachmentNote}`.trim();
    if (!instruction) return;
    followMessagesRef.current = true;
    recordMessage({ role: 'user', text: instruction });
    setSubmitting(true);
    try {
      const uploaded = attachments.length > 0 ? await uploadPending() : [];
      const ok = await runCompile(instruction, 'initial', uploaded);
      if (ok) {
        setValue('');
        setAttachments([]);
      }
    } catch (error) {
      reportError(error, '上传附件失败', '上传失败');
    } finally {
      if (!activeCompileRef.current) setSubmitting(cancelInFlightRef.current);
    }
  };

  const confirmApply = async () => {
    if (!pending || submitting) return;
    followMessagesRef.current = true;
    recordMessage({ role: 'user', text: '确认方案并生成工作流' });
    const compileId = pending.compile_id;
    const controller = new AbortController();
    beginActiveCompile({ id: compileId, controller, kind: 'apply', instruction: pending.instruction });
    try {
      const response = await applyNlCompile(compileId, controller.signal);
      if (activeCompileRef.current?.id !== compileId) return;
      if (app && (response.status === 'planning' || response.status === 'applying')) {
        await pollActiveCompile(app.id, compileId, pending.instruction, controller.signal);
      } else handleCompileResponse(response, pending.instruction);
    } catch (error) {
      if (!isAbortError(error) && !cancelInFlightRef.current) reportError(error, '生成画布失败', '生成失败');
    } finally {
      finishActiveCompile(compileId);
    }
  };

  const requestCancel = () => {
    if (!pending) return;
    setCancelConfirmOpen(true);
  };

  // 确认取消：关弹窗、清空所有缓存。
  const performCancel = async () => {
    const compileId = pending?.compile_id ?? waitingCompile?.compile_id;
    if (!compileId || cancelInFlightRef.current) return;
    cancelInFlightRef.current = true;
    setSubmitting(true);
    setCancelConfirmOpen(false);
    try {
      await cancelNlCompile(compileId);
      if (useEditorStore.getState().app?.id !== app?.id) return;
      setPending(null);
      setWaitingCompile(null);
      clearDecisionState();
      setSupplement('');
      recordMessage({ role: 'assistant', text: '已取消本次编辑。' });
    } catch (error) {
      reportError(error, '取消自然语言编辑失败', '取消失败');
    } finally {
      cancelInFlightRef.current = false;
      setSubmitting(false);
    }
  };

  const confirmCancel = () => {
    if (activeCompileRef.current) {
      cancelActiveCompile();
      return;
    }
    performCancel();
  };

  // 行 2 输入框提交：在同一 compile 会话内 refine 方案，不关闭弹窗。
  const regenerate = async () => {
    if (!pending || !supplement.trim() || submitting || !app) return;
    followMessagesRef.current = true;
    recordMessage({ role: 'user', text: supplement.trim() });
    const compileId = pending.compile_id;
    const controller = new AbortController();
    beginActiveCompile({ id: compileId, controller, kind: 'regenerate', instruction: pending.instruction });
    try {
      const response = await refineNlCompile(compileId, supplement.trim(), controller.signal);
      if (activeCompileRef.current?.id !== compileId) return;
      if (response.status === 'planning' || response.status === 'applying') {
        const received = await pollActiveCompile(app.id, compileId, pending.instruction, controller.signal);
        if (received) setSupplement('');
      } else {
        handleCompileResponse(response, pending.instruction);
        if (response.status !== 'interrupted') setSupplement('');
      }
    } catch (error) {
      if (!isAbortError(error) && !cancelInFlightRef.current) reportError(error, '重新生成方案失败', '重新生成失败');
    } finally {
      finishActiveCompile(compileId);
    }
  };

  const decisionPanel = waitingCompile ? (
    <div>
      <DecisionPromptPanel
        context={waitingCompile.request.context}
        groups={waitingCompile.request.groups}
        disabled={submitting}
        autoComplete={false}
        autoAdvanceOnSingle={false}
        externallyCompletedGroupIds={completedDecisionGroupIdsValue}
        submittedSummary={submittedDecisionSummary}
        submittedSummaryAction={conversation ? undefined : submittedSummaryAction}
        onAnswersChange={handleDecisionAnswersChange}
        onActiveGroupChange={handleActiveDecisionGroupChange}
      />
      {activeIsLastDecisionGroup && !submittedDecisionSummary ? (
        <button type="button" onClick={() => void submitCompileInput()} disabled={!canSubmit} className="mt-2 flex h-10 w-full items-center justify-center gap-2 rounded-full bg-black text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-35">
          提交回答
        </button>
      ) : null}
      {conversation && !submitting && <button type="button" onClick={performCancel} className="mt-2 min-h-9 px-2 text-xs text-black/50 hover:text-black">取消本次编辑</button>}
    </div>
  ) : null;
  const activityIndicator = activeCompile ? (
    <AiActivityIndicator status="working" label={({ initial: '正在生成方案', restore: '正在恢复生成任务', regenerate: '正在重新生成方案', apply: '正在应用到画布', resume: '正在继续生成方案' } as const)[activeCompile.kind]} compact />
  ) : null;
  const cancelDialog = (
    <ConfirmDialog open={cancelConfirmOpen} onClose={() => setCancelConfirmOpen(false)} onConfirm={confirmCancel} title="终止并取消生成方案？" description="当前方案、补充修改说明和自然语言编辑会话将被取消。取消后需要重新提交需求生成方案。" cancelLabel="返回方案" confirmLabel="终止并取消" tone="danger" />
  );

  if (conversation) return (
    <section aria-label="工作流编辑对话" className="flex h-full min-h-0 flex-col bg-white">
      <div ref={conversationRef} onScroll={(event) => { const el = event.currentTarget; followMessagesRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; }} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-5 py-6 lg:px-8">
          {messages.length === 0 && !submitting && !restoring ? (
            <div className="flex flex-1 flex-col justify-center pb-12">
              <h2 className="text-2xl font-semibold tracking-tight text-black/85">{empty ? '从一个想法开始' : '继续完善你的工作流'}</h2>
              <p className="mt-3 text-sm leading-6 text-black/45">{empty ? '描述目标，或附上参考资料。' : '描述想调整的内容，确认方案后应用。'}</p>
            </div>
          ) : <div className="space-y-7" aria-live="polite" aria-relevant="additions">
            {messages.map((message) => (
              <article key={message.id} className={message.role === 'user' ? 'ml-auto w-fit max-w-[90%]' : 'min-w-0'}>
                <div className={`mb-2 text-[11px] font-medium ${message.role === 'user' ? 'text-right text-black/40' : 'text-black/45'}`}>{message.role === 'user' ? '你' : 'Mira'}</div>
                {message.text && <div role={message.error ? 'alert' : undefined} className={`whitespace-pre-wrap break-words text-sm leading-7 ${message.role === 'user' ? 'rounded-2xl bg-black/[0.045] px-4 py-2.5' : message.error ? 'rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-red-700' : 'text-black/75'}`}>{message.text}</div>}
                {message.plan && <section aria-label="编辑方案" className="space-y-4 border-l-2 border-black/10 pl-4 text-sm leading-6 text-black/80">
                  <PlanSection title="目标摘要" items={[message.plan.goal_summary]} />
                  <PlanSection title="这次要改什么" items={compactPlanChanges(message.plan)} />
                  {pending && message.id === planMessageId && <div className="flex flex-wrap gap-2 pt-1">
                    <button type="button" onClick={confirmApply} disabled={submitting} className="min-h-10 rounded-xl bg-black px-4 text-sm font-medium text-white hover:bg-black/85 disabled:opacity-40">确认方案并生成工作流</button>
                    <button type="button" onClick={requestCancel} disabled={submitting} className="min-h-10 rounded-xl border border-black/10 px-4 text-sm text-black/55 hover:bg-black/5 disabled:opacity-40">取消方案</button>
                  </div>}
                </section>}
                {message.decision && (waitingCompile && message.id === decisionMessageId && !submittedDecisionSummary ? decisionPanel : <div className="space-y-2 text-sm leading-6 text-black/70">
                  <div className="font-medium">{message.decision.context?.title}</div>
                  <div>{message.decision.context?.summary}</div>
                  {message.decision.groups.map((group) => <div key={group.id}>{group.label}</div>)}
                </div>)}
              </article>
            ))}
          </div>}
          {(submitting || restoring) && <div className="mt-6 text-xs text-black/55" role="status">{activityIndicator ?? (restoring ? '正在恢复编辑任务…' : cancelInFlightRef.current ? '正在取消…' : '正在上传附件…')}</div>}
        </div>
      </div>
      <div className="shrink-0 bg-white px-5 pb-5 pt-3 lg:px-8">
        <div className="mx-auto w-full max-w-3xl">
          <PillInputBar
            value={waitingCompile ? (submittedDecisionSummary ? '' : activeDecisionText) : pending ? supplement : value}
            onChange={waitingCompile ? (next) => updateActiveDecisionDraft({ text: next }) : pending ? setSupplement : setValue}
            onSubmit={pending ? () => void regenerate() : submit}
            onCancel={activeCompile ? cancelActiveCompile : undefined}
            placeholder={waitingCompile ? '输入补充说明' : pending ? '补充修改说明，调整这份方案' : empty ? '描述你想搭建什么' : '描述要调整的内容'}
            ariaLabel={pending ? '调整方案' : waitingCompile ? '提交回答' : '发送编辑请求'}
            canSubmit={waitingCompile ? canSubmit : pending ? !!supplement.trim() && !submitting : canSubmit}
            submitting={submitting}
            allowAttachments={!pending && (!waitingCompile || activeAllowsFiles)}
            attachments={pending ? [] : waitingCompile ? (submittedDecisionSummary ? [] : activeDecisionAttachments) : attachments}
            onAttachmentsChange={waitingCompile ? (next) => updateActiveDecisionDraft({ attachments: next }) : setAttachments}
            onAttachmentUpdate={waitingCompile ? (id, patch) => setDecisionDrafts((current) => patchDraftAttachment(current, id, patch)) : undefined}
            readOnly={submitting}
            hideSubmit={!!waitingCompile && !activeCompile}
          />
        </div>
      </div>
      {cancelDialog}
    </section>
  );

  return (
    <div className="relative">
      {toast && (
        <div className="absolute -top-10 left-1/2 -translate-x-1/2 text-xs bg-black text-white px-3 py-1.5 rounded-full shadow-pill">
          {toast}
        </div>
      )}
      <PillInputBar
        value={waitingCompile ? (submittedDecisionSummary ? '' : activeAllowsText ? activeDecisionText : '') : value}
        onChange={waitingCompile ? (next) => updateActiveDecisionDraft({ text: next }) : setValue}
        onSubmit={submit}
        onCancel={activeCompile ? cancelActiveCompile : undefined}
        placeholder={placeholder}
        canSubmit={waitingCompile ? false : canSubmit}
        submitting={submitting && !pending}
        allowAttachments={waitingCompile ? activeAllowsFiles : true}
        attachments={waitingCompile ? (submittedDecisionSummary ? [] : activeAllowsFiles ? activeDecisionAttachments : []) : attachments}
        onAttachmentsChange={waitingCompile ? (next) => updateActiveDecisionDraft({ attachments: next }) : setAttachments}
        onAttachmentUpdate={waitingCompile ? (id, patch) => setDecisionDrafts((current) => patchDraftAttachment(current, id, patch)) : undefined}
        readOnly={submitting}
        hideSubmit={!!waitingCompile}
        topSlot={waitingCompile ? decisionPanel : activeCompile ? <div className="mb-2 px-2 text-xs text-black/55">{activityIndicator}</div> : null}
      />

      <AppDialog
        open={!!pending}
        onClose={requestCancel}
        title="确认生成方案"
        description="确认后才会生成并写入画布。"
        widthClassName="max-w-2xl"
      >
        {pending && (
          <div className="space-y-4">
            {/* 主体：只展示本次改动重点，超出滚动。 */}
            <div className="max-h-[360px] overflow-y-auto rounded-2xl border border-black/10 bg-white px-5 py-4">
              <div className="space-y-4 text-sm leading-6 text-black/80">
                <PlanSection title="目标摘要" items={[pending.plan.goal_summary]} />
                <PlanSection title="这次要改什么" items={compactPlanChanges(pending.plan)} />
              </div>
            </div>

            {/* 操作区：垂直三行 */}
            <div className="flex flex-col gap-2">
              {/* 行 1：确定实施 */}
              <button
                type="button"
                onClick={applyingPlan ? cancelActiveCompile : confirmApply}
                disabled={submitting && !applyingPlan}
                className={`flex h-10 w-full items-center justify-center gap-2 rounded-full text-sm font-medium text-white transition disabled:cursor-not-allowed disabled:opacity-50 ${
                  applyingPlan ? 'bg-red-600 hover:bg-red-700' : 'bg-black hover:bg-black/85'
                }`}
              >
                {applyingPlan ? (
                  <>
                    <StopIcon className="h-3.5 w-3.5" />
                    正在生成画布，点击停止
                  </>
                ) : (
                  '确认方案并生成画布'
                )}
              </button>

              {/* 行 2：补充修改说明输入框（pill 样式复用，但状态/逻辑独立于底部输入框） */}
              <PillInputBar
                value={supplement}
                onChange={setSupplement}
                onSubmit={() => void regenerate()}
                onCancel={regeneratingPlan ? cancelActiveCompile : undefined}
                placeholder="补充修改说明，回车重新生成"
                canSubmit={!!supplement.trim() && !submitting}
                submitting={regeneratingPlan}
                readOnly={applyingPlan || regeneratingPlan}
                ariaLabel="重新生成"
              />

              {/* 行 3：取消 */}
              <button
                type="button"
                onClick={requestCancel}
                disabled={submitting}
                className="h-10 w-full rounded-full text-sm text-black/55 transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
              >
                取消
              </button>
            </div>
          </div>
        )}
      </AppDialog>
      {cancelDialog}
    </div>
  );
}
