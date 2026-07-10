import { useRunStore } from '../../stores/useRunStore';
import type { WorkflowNode } from '../../types';

const EMPTY_NODES: WorkflowNode[] = [];

export function useRunProgress() {
  const steps = useRunStore((s) => s.steps);
  const status = useRunStore((s) => s.status);
  const nodes = useRunStore((s) => s.runGraph?.nodes ?? EMPTY_NODES);
  if (nodes.length === 0) return 0;
  if (status === 'success') return 1;
  const finished = nodes.reduce(
    (acc, n) =>
      acc +
      (steps[n.id]?.status === 'success' ||
      steps[n.id]?.status === 'failed' ||
      steps[n.id]?.status === 'cancelled' ||
      steps[n.id]?.status === 'interrupted'
        ? 1
        : 0),
    0,
  );
  return finished / nodes.length;
}
