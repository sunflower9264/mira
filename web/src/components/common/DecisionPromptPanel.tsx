import { useEffect, useMemo, useState, type ReactNode } from 'react';
import type { DecisionAnswer, DecisionGroup, DecisionRequestContext } from '../../types';
import { ASK_USER_NONE_OPTION, type DecisionSubmittedSummary } from './decisionInput';
import { ChevronLeftIcon, ChevronRightIcon } from './Icons';

export function completeDecisionAnswers(
  groups: DecisionGroup[],
  answers: DecisionAnswer[],
): DecisionAnswer[] | null {
  const byId = new Map(answers.map((answer) => [answer.group_id, answer.selected]));
  const completed: DecisionAnswer[] = [];
  for (const group of groups) {
    const selected = byId.get(group.id) ?? [];
    if (group.type === 'single' && selected.length !== 1) return null;
    if (group.type === 'multi' && selected.length < 1) return null;
    completed.push({ group_id: group.id, selected });
  }
  return completed;
}

function answerFor(answers: DecisionAnswer[], groupId: string): string[] {
  return answers.find((answer) => answer.group_id === groupId)?.selected ?? [];
}

function setAnswer(
  answers: DecisionAnswer[],
  groupId: string,
  selected: string[],
): DecisionAnswer[] {
  const exists = answers.some((answer) => answer.group_id === groupId);
  if (!exists) return [...answers, { group_id: groupId, selected }];
  return answers.map((answer) =>
    answer.group_id === groupId ? { ...answer, selected } : answer,
  );
}

export function DecisionPromptPanel({
  context,
  groups,
  disabled = false,
  autoComplete = true,
  autoAdvanceOnSingle = true,
  externallyCompletedGroupIds = [],
  submittedSummary = null,
  submittedSummaryAction,
  onComplete,
  onAnswersChange,
  onActiveGroupChange,
}: {
  context: DecisionRequestContext;
  groups: DecisionGroup[];
  disabled?: boolean;
  autoComplete?: boolean;
  autoAdvanceOnSingle?: boolean;
  externallyCompletedGroupIds?: string[];
  submittedSummary?: DecisionSubmittedSummary | null;
  submittedSummaryAction?: ReactNode;
  onComplete?(answers: DecisionAnswer[]): void;
  onAnswersChange?(answers: DecisionAnswer[]): void;
  onActiveGroupChange?(groupId: string, index: number): void;
}) {
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<DecisionAnswer[]>([]);

  useEffect(() => {
    setIndex(0);
    setAnswers([]);
    onAnswersChange?.([]);
  }, [groups]);

  const group = groups[index];
  const total = groups.length;
  const externallyCompleted = useMemo(
    () => new Set(externallyCompletedGroupIds),
    [externallyCompletedGroupIds],
  );

  useEffect(() => {
    if (!group) return;
    onActiveGroupChange?.(group.id, index);
  }, [group?.id, index, onActiveGroupChange]);

  const selected = group ? answerFor(answers, group.id) : [];
  const optionComplete = group
    ? group.type === 'single'
      ? selected.length === 1
      : selected.length > 0
    : false;
  const currentComplete = group
    ? optionComplete || externallyCompleted.has(group.id)
    : false;
  const completedCount = useMemo(
    () => groups.filter((item) => answerFor(answers, item.id).length > 0 || externallyCompleted.has(item.id)).length,
    [answers, externallyCompleted, groups],
  );

  if (!group) return null;

  if (submittedSummary) {
    const selectedLabels = groups.flatMap((item) => answerFor(submittedSummary.answers, item.id));
    const supplements = Object.values(submittedSummary.supplements ?? {}).flatMap((item) => {
      const parts: string[] = [];
      if (item.text?.trim()) parts.push(item.text.trim());
      if (item.fileNames?.length) parts.push(`文件：${item.fileNames.join('、')}`);
      return parts;
    });

    return (
      <section className="rounded-2xl border border-black/10 bg-white px-4 py-3 shadow-card">
        <div className="text-sm leading-6 text-black/80">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="mb-2">
                <div className="text-xs font-semibold leading-5 text-black/85">{context.title}</div>
                <p className="mt-0.5 text-xs leading-5 text-black/55">{context.summary}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="shrink-0 text-xs font-medium text-black/45">已选择：</span>
                {selectedLabels.length > 0 ? (
                  selectedLabels.map((label, labelIndex) => (
                    <span
                      key={`${label}-${labelIndex}`}
                      className="inline-flex max-w-full items-center rounded-full bg-black/[0.06] px-2.5 py-1 text-xs font-medium text-black/70 ring-1 ring-black/5"
                    >
                      <span className="truncate">{label}</span>
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-black/45">无</span>
                )}
              </div>
            </div>
            {submittedSummaryAction ? (
              <div className="shrink-0">{submittedSummaryAction}</div>
            ) : null}
          </div>
          {supplements.length > 0 ? (
            <div className="mt-2 flex gap-2 border-t border-black/5 pt-2">
              <span className="shrink-0 text-xs font-medium text-black/45">已补充：</span>
              <span className="min-w-0 break-words text-xs text-black/70">
                {supplements.join('；')}
              </span>
            </div>
          ) : null}
        </div>
      </section>
    );
  }

  const commitAnswers = (next: DecisionAnswer[]) => {
    setAnswers(next);
    onAnswersChange?.(next);
  };

  const finishOrAdvance = (next: DecisionAnswer[]) => {
    if (index >= total - 1) {
      if (!autoComplete) return;
      const completed = completeDecisionAnswers(groups, next);
      if (completed) onComplete?.(completed);
      return;
    }
    setIndex((current) => Math.min(current + 1, total - 1));
  };

  const selectOption = (optionLabel: string) => {
    if (disabled) return;
    const nextSelected =
      group.type === 'multi'
        ? optionLabel === ASK_USER_NONE_OPTION
          ? selected.includes(optionLabel)
            ? []
            : [optionLabel]
          : selected.includes(optionLabel)
            ? selected.filter((item) => item !== optionLabel)
            : [...selected.filter((item) => item !== ASK_USER_NONE_OPTION), optionLabel]
        : selected.includes(optionLabel)
          ? []
          : [optionLabel];
    const nextAnswers = setAnswer(answers, group.id, nextSelected);
    commitAnswers(nextAnswers);
    if (group.type === 'single' && nextSelected.length > 0 && autoAdvanceOnSingle) finishOrAdvance(nextAnswers);
  };

  const goPrevious = () => {
    if (disabled) return;
    setIndex((current) => Math.max(current - 1, 0));
  };

  const goNext = () => {
    if (disabled || !currentComplete) return;
    finishOrAdvance(answers);
  };
  const nextDisabled = disabled || !currentComplete || (!autoComplete && index >= total - 1);

  return (
    <section className="rounded-2xl border border-black/10 bg-white px-4 py-3 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold leading-5 text-black/85">
            {context.title}
          </h3>
          <p className="mt-1 text-xs leading-5 text-black/55">
            {context.summary}
          </p>
          <div className="mt-3 text-[11px] font-medium uppercase tracking-wider text-amber-600">
            第 {index + 1}/{total} 个问题
          </div>
          <h4 className="mt-1 text-sm font-semibold leading-5 text-black/85">
            {group.label}
          </h4>
          {group.placeholder ? (
            <p className="mt-1 text-xs leading-5 text-black/50">{group.placeholder}</p>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={goPrevious}
            disabled={disabled || index === 0}
            aria-label="上一组"
            className="grid h-7 w-7 place-items-center rounded-full text-black/50 transition hover:bg-black/5 hover:text-black/85 disabled:cursor-not-allowed disabled:opacity-25 disabled:hover:bg-transparent"
          >
            <ChevronLeftIcon className="h-4 w-4" />
          </button>
          <span className="min-w-10 text-center text-xs tabular-nums text-black/45">
            {index + 1}/{total}
          </span>
          <button
            type="button"
            onClick={goNext}
            disabled={nextDisabled}
            aria-label={index >= total - 1 ? (autoComplete ? '提交回答' : '已到最后一组') : '下一组'}
            className="grid h-7 w-7 place-items-center rounded-full text-black/50 transition hover:bg-black/5 hover:text-black/85 disabled:cursor-not-allowed disabled:opacity-25 disabled:hover:bg-transparent"
          >
            <ChevronRightIcon className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="mt-3 space-y-2">
        {group.options.map((option) => {
          const isSelected = selected.includes(option.label);
          return (
            <button
              key={option.label}
              type="button"
              onClick={() => selectOption(option.label)}
              disabled={disabled}
              aria-pressed={isSelected}
              className={`flex w-full items-start gap-3 rounded-2xl border px-4 py-3 text-left text-sm transition disabled:cursor-not-allowed disabled:opacity-55 ${
                isSelected
                  ? 'border-black bg-black text-white shadow-card'
                  : 'border-black/10 bg-white text-black/80 hover:border-black/25 hover:bg-black/[0.02]'
              }`}
            >
              <span
                className={`mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center border ${
                  group.type === 'multi' ? 'rounded' : 'rounded-full'
                } ${isSelected ? 'border-white bg-white' : 'border-black/25'}`}
                aria-hidden="true"
              >
                {isSelected ? (
                  group.type === 'multi' ? (
                    <span className="block h-2 w-2 rotate-45 border-b-2 border-r-2 border-black" />
                  ) : (
                    <span className="block h-2 w-2 rounded-full bg-black" />
                  )
                ) : null}
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="break-words font-medium leading-snug">{option.label}</span>
                  {option.recommended ? (
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                        isSelected ? 'bg-white/15 text-white' : 'bg-amber-50 text-amber-700'
                      }`}
                    >
                      推荐
                    </span>
                  ) : null}
                </span>
                <span className={`mt-1 block break-words text-xs leading-5 ${isSelected ? 'text-white/75' : 'text-black/55'}`}>
                  {option.description}
                </span>
              </span>
            </button>
          );
        })}
      </div>
      <div className="mt-3 text-right text-[11px] text-black/40">
        已回答 {completedCount}/{total}
      </div>
    </section>
  );
}
