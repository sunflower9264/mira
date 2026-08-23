import type { DecisionAnswer, DecisionGroup } from '../../types';
import type { PillAttachment } from './PillInputBar';

export const ASK_USER_NONE_OPTION = '以上都不是';

interface DecisionSupplementDraft {
  text: string;
  attachments: PillAttachment[];
}

export type DecisionSupplementDrafts = Record<string, DecisionSupplementDraft>;

interface DecisionSubmittedSupplement {
  text?: string;
  fileNames?: string[];
}

export interface DecisionSubmittedSummary {
  answers: DecisionAnswer[];
  supplements?: Record<string, DecisionSubmittedSupplement>;
}

function selectedForDecisionGroup(
  answers: DecisionAnswer[],
  groupId: string,
): string[] {
  return answers.find((answer) => answer.group_id === groupId)?.selected ?? [];
}

function isDecisionOptionComplete(
  group: DecisionGroup,
  answers: DecisionAnswer[],
): boolean {
  const selected = selectedForDecisionGroup(answers, group.id);
  return group.type === 'single' ? selected.length === 1 : selected.length > 0;
}

function isDecisionDraftComplete(
  draft: DecisionSupplementDraft | undefined,
): boolean {
  if (!draft) return false;
  return draft.text.trim().length > 0 || draft.attachments.length > 0;
}

export function completedDecisionGroupIds(
  groups: DecisionGroup[],
  answers: DecisionAnswer[],
): string[] {
  return groups
    .filter((group) => isDecisionOptionComplete(group, answers))
    .map((group) => group.id);
}

function hasDecisionSupplementDrafts(
  groups: DecisionGroup[],
  drafts: DecisionSupplementDrafts,
): boolean {
  return groups.some((group) => isDecisionDraftComplete(drafts[group.id]));
}

export function buildDecisionSupplementText(
  groups: DecisionGroup[],
  answers: DecisionAnswer[],
  drafts: DecisionSupplementDrafts,
): string | undefined {
  if (!hasDecisionSupplementDrafts(groups, drafts)) return undefined;

  const blocks = groups.flatMap((group) => {
    const selected = selectedForDecisionGroup(answers, group.id);
    const draft = drafts[group.id];
    const lines = [`问题：${group.label}`];
    if (selected.length > 0) lines.push(`选择：${selected.join('、')}`);
    if (draft?.text.trim()) lines.push(`补充：${draft.text.trim()}`);
    if (draft?.attachments.length) {
      lines.push(`附件：${draft.attachments.map((item) => item.name).join('、')}`);
    }
    return lines.length > 1 ? [lines.join('\n')] : [];
  });

  if (!blocks.length) return undefined;
  return `用户对 ask_user 的逐题回答：\n\n${blocks.join('\n\n')}`;
}

export function buildDecisionSubmittedSummary(
  groups: DecisionGroup[],
  answers: DecisionAnswer[],
  drafts: DecisionSupplementDrafts = {},
): DecisionSubmittedSummary {
  const supplements: Record<string, DecisionSubmittedSupplement> = {};
  for (const group of groups) {
    const draft = drafts[group.id];
    if (!draft) continue;
    const text = draft.text.trim();
    const fileNames = draft.attachments.map((item) => item.name).filter(Boolean);
    if (text || fileNames.length > 0) {
      supplements[group.id] = {
        ...(text ? { text } : {}),
        ...(fileNames.length > 0 ? { fileNames } : {}),
      };
    }
  }
  return {
    answers,
    supplements: Object.keys(supplements).length > 0 ? supplements : undefined,
  };
}
