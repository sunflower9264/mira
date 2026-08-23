// "Preview" tab — runs the app inline. Shares the run logic with App View by
// driving the same useRunStore.

import { useEditorStore } from '../../stores/useEditorStore';
import { AppRunContent } from './AppRunContent';

export function PreviewTab({ hideRunFailureError = false }: { hideRunFailureError?: boolean }) {
  const app = useEditorStore((s) => s.app);
  const setGraph = useEditorStore((s) => s.setGraph);

  if (!app) return null;

  return (
    <AppRunContent
      app={app}
      variant="preview"
      failureErrorPlacement={hideRunFailureError ? 'hidden' : 'bottom'}
      onToolsChange={(disabledToolIds) => {
        setGraph({
          ...app.graph,
          tools: { disabled_tool_ids: disabledToolIds },
        });
      }}
    />
  );
}
