// 发布按钮：根据 app.status 与未发布更改感知，呈现三态。

import { useEffect, useState } from 'react';
import { Menu, MenuButton, MenuItem, MenuItems } from '@headlessui/react';
import { useEditorStore } from '../../stores/useEditorStore';
import * as api from '../../lib/api';
import { blockingWorkflowLintMessage } from '../../lib/workflowLint';
import { ConfirmDialog } from './ConfirmDialog';
import { CloudIcon } from './Icons';
import { WorkflowLintNotice } from './WorkflowLintNotice';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import type { App, WorkflowLintResult } from '../../types';

interface PublishButtonProps {
  app: App | null;
}

type Action = 'publish' | 'republish' | 'unpublish';
type PublishMode = 'cloneable' | 'run_only' | 'private';

function hasUnpublishedChanges(app: App): boolean {
  if (app.status !== 'published') return false;
  if (!app.published_at) return false;
  return new Date(app.updated_at).getTime() > new Date(app.published_at).getTime();
}

const ACTION_CONFIG: Record<
  Action,
  { title: string; description: string; confirmLabel: string; tone: 'default' | 'danger' }
> = {
  publish: {
    title: '发布应用',
    description: '发布后，应用会标记为已发布，并自动记录一条版本快照。',
    confirmLabel: '发布',
    tone: 'default',
  },
  republish: {
    title: '发布更新',
    description: '将记录一条新的发布快照，作为最新的已发布版本。',
    confirmLabel: '发布更新',
    tone: 'default',
  },
  unpublish: {
    title: '撤销发布',
    description: '应用会回到草稿状态，已记录的发布快照不会被删除。',
    confirmLabel: '撤销发布',
    tone: 'danger',
  },
};

export function PublishButton({ app }: PublishButtonProps) {
  const publish = useEditorStore((s) => s.publish);
  const unpublish = useEditorStore((s) => s.unpublish);
  const [pending, setPending] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [lintResult, setLintResult] = useState<WorkflowLintResult | null>(null);
  const [lintLoading, setLintLoading] = useState(false);
  const [lintError, setLintError] = useState<string | null>(null);
  const [publishMode, setPublishMode] = useState<PublishMode>('cloneable');

  const pendingNeedsLint = pending === 'publish' || pending === 'republish';

  const openAction = (action: Action) => {
    if (action === 'publish' || action === 'republish') {
      setPublishMode(modeFromApp(app));
    }
    setPending(action);
  };

  useEffect(() => {
    if (!app) return;
    if (!pendingNeedsLint) {
      setLintResult(null);
      setLintLoading(false);
      setLintError(null);
      return;
    }
    const controller = new AbortController();
    setLintLoading(true);
    setLintError(null);
    void api.lintAppGraph(app.id, app.graph, controller.signal)
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
  }, [app?.graph, app?.id, pendingNeedsLint]);

  if (!app) return null;

  const isPublished = app.status === 'published';
  const hasChanges = hasUnpublishedChanges(app);

  const handleConfirm = async () => {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending === 'publish' || pending === 'republish') {
        const lint = await api.lintAppGraph(app.id, app.graph);
        setLintResult(lint);
        const lintMessage = blockingWorkflowLintMessage(lint);
        if (lintMessage) throw new Error(lintMessage);
        await publish(payloadFromMode(publishMode, app));
      } else {
        await unpublish();
      }
      setPending(null);
    } catch (e) {
      showCaughtError(e, '操作失败');
    } finally {
      setBusy(false);
    }
  };

  const cfg = pending ? ACTION_CONFIG[pending] : null;
  const confirmDisabled = pendingNeedsLint && (lintLoading || (lintResult?.summary.errors ?? 0) > 0);
  const description = cfg ? (
    <div className="space-y-3">
      <p>{cfg.description}</p>
      {pendingNeedsLint && (
        <div className="space-y-2">
          <PublishModeOption
            value="cloneable"
            checked={publishMode === 'cloneable'}
            onChange={setPublishMode}
            title="公开：允许克隆和查看节点"
            description="进入应用市场，其他登录用户可以运行，也可以克隆成自己的草稿。"
          />
          <PublishModeOption
            value="run_only"
            checked={publishMode === 'run_only'}
            onChange={setPublishMode}
            title="公开：仅运行"
            description="进入应用市场，其他登录用户只能运行，看不到节点，也不能克隆。"
          />
          <PublishModeOption
            value="private"
            checked={publishMode === 'private'}
            onChange={setPublishMode}
            title="仅自己可见"
            description="不会展示给其他登录用户。"
          />
        </div>
      )}
      {pendingNeedsLint && <WorkflowLintNotice result={lintResult} loading={lintLoading} error={lintError} />}
    </div>
  ) : null;

  let trigger: React.ReactNode;
  if (!isPublished) {
    trigger = (
      <button
        type="button"
        onClick={() => openAction('publish')}
        className="inline-flex items-center gap-1.5 rounded-full bg-black px-3 py-1.5 text-sm font-medium text-white transition hover:bg-black/85"
      >
        <CloudIcon className="w-4 h-4" />
        <span>发布</span>
      </button>
    );
  } else if (hasChanges) {
    trigger = (
      <button
        type="button"
        onClick={() => openAction('republish')}
        className="relative inline-flex items-center gap-1.5 rounded-full bg-black px-3 py-1.5 text-sm font-medium text-white transition hover:bg-black/85"
      >
        <CloudIcon className="w-4 h-4" />
        <span>发布更新</span>
        <span
          aria-label="有未发布的更改"
          className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-amber-500 ring-2 ring-white"
        />
      </button>
    );
  } else {
    trigger = (
      <Menu as="div" className="relative">
        <MenuButton className="inline-flex items-center gap-1.5 rounded-full border border-black/10 px-3 py-1.5 text-sm font-medium text-black/75 transition hover:bg-black/5 data-[open]:bg-black/5">
          <CloudIcon className="w-4 h-4" />
          <span>已发布</span>
          <span className="text-black/35">▾</span>
        </MenuButton>
        <MenuItems
          anchor="bottom end"
          className="z-50 mt-2 w-44 rounded-2xl bg-white p-1 shadow-[0_20px_70px_rgba(0,0,0,0.18)] ring-1 ring-black/5 focus:outline-none"
        >
          <MenuItem>
            {({ focus }) => (
              <button
                type="button"
                onClick={() => openAction('republish')}
                className={`w-full rounded-xl px-3 py-2 text-left text-sm text-black ${
                  focus ? 'bg-black/5' : ''
                }`}
              >
                重新发布
              </button>
            )}
          </MenuItem>
          <MenuItem>
            {({ focus }) => (
              <button
                type="button"
                onClick={() => openAction('unpublish')}
                className={`w-full rounded-xl px-3 py-2 text-left text-sm text-red-600 ${
                  focus ? 'bg-black/5' : ''
                }`}
              >
                撤销发布
              </button>
            )}
          </MenuItem>
        </MenuItems>
      </Menu>
    );
  }

  return (
    <>
      {trigger}
      <ConfirmDialog
        open={!!pending}
        onClose={() => {
          if (!busy) setPending(null);
        }}
        onConfirm={handleConfirm}
        title={cfg?.title ?? ''}
        description={description}
        confirmLabel={cfg?.confirmLabel}
        tone={cfg?.tone ?? 'default'}
        busy={busy}
        confirmDisabled={confirmDisabled}
      />
    </>
  );
}

function modeFromApp(app: App | null): PublishMode {
  if (!app) return 'cloneable';
  if (app.visibility === 'private') return 'private';
  return app.market_access === 'run_only' ? 'run_only' : 'cloneable';
}

function payloadFromMode(mode: PublishMode, app: App): { visibility: App['visibility']; market_access: App['market_access'] } {
  if (mode === 'private') {
    return { visibility: 'private', market_access: app.market_access ?? 'cloneable' };
  }
  return {
    visibility: 'public',
    market_access: mode === 'run_only' ? 'run_only' : 'cloneable',
  };
}

function PublishModeOption({
  value,
  checked,
  onChange,
  title,
  description,
}: {
  value: PublishMode;
  checked: boolean;
  onChange(value: PublishMode): void;
  title: string;
  description: string;
}) {
  return (
    <label className={`flex items-start gap-3 rounded-2xl border p-3 text-left transition ${
      checked ? 'border-black/25 bg-black/[0.04]' : 'border-black/10 bg-black/[0.02]'
    }`}>
      <input
        type="radio"
        name="publish-mode"
        checked={checked}
        onChange={() => onChange(value)}
        className="mt-0.5 h-4 w-4 accent-black"
      />
      <span>
        <span className="block text-sm font-medium text-black">{title}</span>
        <span className="mt-1 block text-xs leading-5 text-black/55">{description}</span>
      </span>
    </label>
  );
}
