import { useCallback, useEffect, useState } from 'react';
import * as api from '../../lib/api';
import { isCancellableRunStatus, useRunStore } from '../../stores/useRunStore';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import type { DecisionAnswer } from '../../types';
import { completeDecisionAnswers, DecisionPromptPanel } from '../common/DecisionPromptPanel';
import {
  buildDecisionSubmittedSummary,
  buildDecisionSupplementText,
  completedDecisionGroupIds,
  type DecisionSubmittedSummary,
  type DecisionSupplementDrafts,
} from '../common/decisionInput';
import { PillInputBar, type PillAttachment } from '../common/PillInputBar';
import { StopIcon } from '../common/Icons';

export function WaitingInputPanel() {
  const waitingInput = useRunStore((s) => s.waitingInput);
  const runId = useRunStore((s) => s.runId);
  const status = useRunStore((s) => s.status);
  const submitWaitingInput = useRunStore((s) => s.submitWaitingInput);
  const cancel = useRunStore((s) => s.cancel);
  const [decisionAnswers, setDecisionAnswers] = useState<DecisionAnswer[]>([]);
  const [activeDecisionGroupId, setActiveDecisionGroupId] = useState('');
  const [activeDecisionGroupIndex, setActiveDecisionGroupIndex] = useState(0);
  const [decisionDrafts, setDecisionDrafts] = useState<DecisionSupplementDrafts>({});
  const [text, setText] = useState('');
  const [attachments, setAttachments] = useState<PillAttachment[]>([]);
  const [submittedDecisionSummary, setSubmittedDecisionSummary] = useState<DecisionSubmittedSummary | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState(false);

  // 切换 waitingInput 时清空旧状态，避免上一次输入残留。
  useEffect(() => {
    setDecisionAnswers([]);
    setActiveDecisionGroupId(waitingInput?.groups[0]?.id ?? '');
    setActiveDecisionGroupIndex(0);
    setDecisionDrafts({});
    setText('');
    setAttachments([]);
    setSubmittedDecisionSummary(null);
  }, [waitingInput?.tool_use_id, waitingInput?.node_id, waitingInput?.groups]);

  if (!waitingInput) return null;

  const groups = waitingInput.groups ?? [];
  const canStop = runId !== null && isCancellableRunStatus(status);

  const completedAnswers = groups.length > 0 ? completeDecisionAnswers(groups, decisionAnswers) : [];
  const completedDecisionGroupIdsValue = completedDecisionGroupIds(
    groups,
    decisionAnswers,
  );
  const allDecisionGroupsComplete = groups.length > 0 && completedDecisionGroupIdsValue.length === groups.length;
  const effectiveDecisionGroupId =
    activeDecisionGroupId || groups[activeDecisionGroupIndex]?.id || groups[0]?.id || '';
  const activeDecisionDraft = effectiveDecisionGroupId ? decisionDrafts[effectiveDecisionGroupId] : undefined;
  const activeDecisionText = activeDecisionDraft?.text ?? '';
  const activeDecisionAttachments = activeDecisionDraft?.attachments ?? [];
  const activeAllowsText = !!effectiveDecisionGroupId;
  const activeAllowsFiles = !!effectiveDecisionGroupId;
  const activeIsLastDecisionGroup = groups.length > 0 && activeDecisionGroupIndex >= groups.length - 1;
  const showSubmitButton = groups.length > 0 ? activeIsLastDecisionGroup && submittedDecisionSummary === null : true;
  const hideSupplementInput = groups.length > 0 && submittedDecisionSummary !== null;

  const canSubmitInput =
    (groups.length > 0
      ? allDecisionGroupsComplete && activeIsLastDecisionGroup
      : text.trim().length > 0 || attachments.length > 0) &&
    !submitting;

  const updateActiveDecisionDraft = (patch: Partial<{ text: string; attachments: PillAttachment[] }>) => {
    if (!effectiveDecisionGroupId) return;
    setDecisionDrafts((current) => {
      const previous = current[effectiveDecisionGroupId] ?? { text: '', attachments: [] };
      return {
        ...current,
        [effectiveDecisionGroupId]: { ...previous, ...patch },
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

  // 上传新增的附件，复用已上传过的（带 uploadId）的引用。
  const uploadPending = async (): Promise<{ id: string; name?: string }[]> => {
    const refs: { id: string; name?: string }[] = [];
    const next = [...attachments];
    for (let i = 0; i < next.length; i += 1) {
      const item = next[i];
      if (item.uploadId) {
        refs.push({ id: item.uploadId, name: item.name });
        continue;
      }
      if (!item.file) throw new Error(`附件「${item.name}」缺少文件内容`);
      const result = await api.uploadFile(item.file);
      next[i] = { ...item, uploadId: result.id };
      refs.push({ id: result.id, name: item.name });
    }
    setAttachments(next);
    return refs;
  };

  const uploadDecisionDrafts = async (
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
        const result = await api.uploadFile(item.file);
        attachmentsForGroup[i] = { ...item, uploadId: result.id };
        refs.push({ id: result.id, name: item.name });
      }
      next[group.id] = { ...draft, attachments: attachmentsForGroup };
    }
    setDecisionDrafts(next);
    return { refs, drafts: next };
  };

  // 统一提交选项、文本和附件，避免选项自动提交时丢失补充文件。
  const submitInput = async () => {
    if (!canSubmitInput) return;
    const answers = groups.length > 0 ? completedAnswers ?? [] : [];
    if (groups.length > 0) {
      setSubmittedDecisionSummary(buildDecisionSubmittedSummary(groups, answers, decisionDrafts));
    }
    setSubmitting(true);
    try {
      const { refs: uploaded, drafts: uploadedDrafts } = groups.length > 0
        ? await uploadDecisionDrafts(decisionDrafts)
        : { refs: attachments.length > 0 ? await uploadPending() : [], drafts: decisionDrafts };
      const groupedText = groups.length > 0
        ? buildDecisionSupplementText(groups, decisionAnswers, uploadedDrafts)
        : undefined;
      await submitWaitingInput({
        answers,
        text: groupedText ?? (text.trim() || undefined),
        attachments: uploaded,
      });
    } catch (error) {
      setSubmittedDecisionSummary(null);
      showCaughtError(error, '提交失败', '提交失败');
    } finally {
      setSubmitting(false);
    }
  };
  const submittedSummaryAction = submittedDecisionSummary && submitting && canStop ? (
    <button
      type="button"
      onClick={() => {
        setStopping(true);
        void cancel().finally(() => setStopping(false));
      }}
      disabled={stopping}
      className="inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-full bg-red-600 px-3 text-xs font-medium text-white transition hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-600/40"
      aria-label="终止"
      title="终止"
    >
      <StopIcon className="h-3.5 w-3.5" />
      终止
    </button>
  ) : null;

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-black/10 bg-white shadow-card">
      <header className="shrink-0 px-6 pt-6 pb-4">
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-medium uppercase tracking-wider text-amber-600">
              等待补充输入
            </div>
            <h3 className="mt-1.5 text-lg font-semibold text-black/85">
              等待你的选择
            </h3>
          </div>
          {canStop ? (
            <button
              type="button"
              aria-label="停止运行"
              title="停止运行"
              className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-red-50 text-red-600 transition-transform duration-150 ease-out hover:scale-105 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:scale-100"
              onClick={() => {
                setStopping(true);
                void cancel().finally(() => setStopping(false));
              }}
              disabled={stopping}
            >
              <StopIcon className="h-4 w-4" />
            </button>
          ) : null}
        </div>
      </header>

      {/* 选项区：撑满剩余空间，溢出可滚动；没有 groups 时占位让输入框落到底部。 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-6">
        {groups.length > 0 ? (
          <DecisionPromptPanel
            context={waitingInput.context}
            groups={groups}
            disabled={submitting}
            autoComplete={false}
            autoAdvanceOnSingle={false}
            externallyCompletedGroupIds={completedDecisionGroupIdsValue}
            submittedSummary={submittedDecisionSummary}
            submittedSummaryAction={submittedSummaryAction}
            onAnswersChange={handleDecisionAnswersChange}
            onActiveGroupChange={handleActiveDecisionGroupChange}
          />
        ) : null}
      </div>

      {/* 输入框（含附件）：发送按钮统一提交选项、文字和文件。 */}
      <div className={`shrink-0 px-6 pt-3 pb-6 ${submitting ? 'opacity-75' : ''}`}>
        {showSubmitButton ? (
          <button
            type="button"
            onClick={() => {
              if (submitting) {
                setStopping(true);
                void cancel().finally(() => setStopping(false));
              } else {
                void submitInput();
              }
            }}
            disabled={submitting ? stopping || !canStop : !canSubmitInput}
            className={`${hideSupplementInput ? '' : 'mb-2'} flex h-10 w-full items-center justify-center gap-2 rounded-full text-sm font-medium text-white transition disabled:cursor-not-allowed disabled:opacity-35 ${
              submitting ? 'bg-red-600 hover:bg-red-700' : 'bg-black hover:bg-black/85 disabled:hover:bg-black'
            }`}
          >
            {submitting ? (
              <>
                <StopIcon className="h-3.5 w-3.5" />
                终止
              </>
            ) : (
              '提交回答'
            )}
          </button>
        ) : null}
        {hideSupplementInput ? null : (
          <PillInputBar
            value={groups.length > 0 ? (activeAllowsText ? activeDecisionText : '') : submitting ? '' : text}
            onChange={groups.length > 0 ? (next) => updateActiveDecisionDraft({ text: next }) : setText}
            onSubmit={() => void submitInput()}
            placeholder={groups.length > 0 ? '输入补充说明...' : '继续运行需要的内容…'}
            canSubmit={false}
            submitting={submitting}
            ariaLabel="发送输入内容"
            allowAttachments={groups.length > 0 ? activeAllowsFiles : true}
            attachments={groups.length > 0 ? (activeAllowsFiles ? activeDecisionAttachments : []) : submitting ? [] : attachments}
            onAttachmentsChange={groups.length > 0 ? (next) => updateActiveDecisionDraft({ attachments: next }) : setAttachments}
            readOnly={submitting}
            hideSubmit
          />
        )}
      </div>
    </section>
  );
}
