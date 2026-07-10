import type { WorkflowLintResult } from '../../types';
import { formatWorkflowLintIssue, visibleWorkflowLintIssues } from '../../lib/workflowLint';

interface WorkflowLintNoticeProps {
  result: WorkflowLintResult | null;
  loading?: boolean;
  error?: string | null;
  maxItems?: number;
}

export function WorkflowLintNotice({
  result,
  loading = false,
  error = null,
  maxItems = 4,
}: WorkflowLintNoticeProps) {
  if (loading) {
    return (
      <section className="rounded-xl border border-black/10 bg-white px-3 py-2 text-xs text-black/50">
        正在预检工作流...
      </section>
    );
  }
  if (error) {
    return (
      <section className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        工作流预检失败：{error}
      </section>
    );
  }
  if (!result) return null;

  const issues = visibleWorkflowLintIssues(result);
  if (issues.length === 0) return null;

  const visible = issues.slice(0, maxItems);
  const errors = issues.filter((issue) => issue.severity === 'error').length;
  const warnings = issues.filter((issue) => issue.severity === 'warning').length;
  const hasErrors = errors > 0;
  const tone = hasErrors
    ? 'border-red-200 bg-red-50 text-red-800'
    : 'border-amber-200 bg-amber-50 text-amber-800';

  return (
    <section className={`rounded-xl border px-3 py-2 text-xs ${tone}`}>
      <div className="font-medium">
        {hasErrors
          ? `预检发现 ${errors} 个错误`
          : `预检发现 ${warnings} 个提醒`}
      </div>
      <div className="mt-1 space-y-1">
        {visible.map((issue) => (
          <div key={`${issue.code}-${issue.node_id ?? ''}-${issue.edge_id ?? ''}-${issue.detail}`}>
            {formatWorkflowLintIssue(issue)}
          </div>
        ))}
      </div>
      {issues.length > visible.length && (
        <div className="mt-1 opacity-75">还有 {issues.length - visible.length} 项。</div>
      )}
    </section>
  );
}
