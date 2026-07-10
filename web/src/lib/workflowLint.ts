import type { WorkflowLintIssue, WorkflowLintResult } from '../types';

const HIDDEN_WORKFLOW_LINT_CODES = new Set(['graph_empty']);

const NODE_NAME_REPLACEMENTS: Array<[RegExp, string]> = [
  [/\buser_input\s+节点/g, '用户输入节点'],
  [/\bgenerate\s+节点/g, '生成节点'],
  [/\bcondition\s+节点/g, '条件节点'],
  [/\boutput\s+节点/g, '输出节点'],
  [/\basset\s+节点/g, '素材节点'],
  [/\buser_input\b/g, '用户输入节点'],
  [/\bgenerate\b/g, '生成节点'],
  [/\bcondition\b/g, '条件节点'],
  [/\boutput\b/g, '输出节点'],
  [/\basset\b/g, '素材节点'],
];

export function visibleWorkflowLintIssues(result: WorkflowLintResult): WorkflowLintIssue[] {
  return result.issues.filter((issue) => !HIDDEN_WORKFLOW_LINT_CODES.has(issue.code));
}

export function blockingWorkflowLintMessage(result: WorkflowLintResult): string | null {
  const errors = result.issues.filter((issue) => issue.severity === 'error');
  if (errors.length === 0) return null;
  const visibleErrors = errors.filter((issue) => !HIDDEN_WORKFLOW_LINT_CODES.has(issue.code));
  const details = visibleErrors.slice(0, 4).map(formatWorkflowLintIssue).join('\n');
  const suffix = visibleErrors.length > 4 ? `\n还有 ${visibleErrors.length - 4} 个错误。` : '';
  return details ? `工作流预检未通过：\n${details}${suffix}` : '工作流预检未通过';
}

export function formatWorkflowLintIssue(issue: WorkflowLintIssue): string {
  const target = issue.node_id || issue.edge_id;
  return localizeWorkflowLintText(target ? `${issue.title}（${target}）：${issue.detail}` : `${issue.title}：${issue.detail}`);
}

function localizeWorkflowLintText(text: string): string {
  return NODE_NAME_REPLACEMENTS.reduce((current, [pattern, replacement]) => current.replace(pattern, replacement), text);
}
