import { useState } from 'react';
import { BookOpen, ShieldCheck } from 'lucide-react';
import { AppDialog } from './AppDialog';
import type { WikiAccess } from '../../types';

interface WikiAccessDialogProps {
  access: WikiAccess | null;
  appName: string;
  onClose(): void;
  onAllow(): Promise<void>;
  onSkip(): Promise<void>;
}

export function WikiAccessDialog({ access, appName, onClose, onAllow, onSkip }: WikiAccessDialogProps) {
  const [busy, setBusy] = useState<'allow' | 'skip' | null>(null);
  const [error, setError] = useState('');

  const run = async (kind: 'allow' | 'skip', action: () => Promise<void>) => {
    setBusy(kind);
    setError('');
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '操作失败，请重试');
    } finally {
      setBusy(null);
    }
  };

  return (
    <AppDialog
      open={access !== null}
      onClose={busy ? () => undefined : onClose}
      title="允许应用读取你的 Wiki？"
      description={`“${appName}”由其他用户创建。允许后，它可以在运行时读取你当前 Wiki 的冻结副本。`}
      dismissible={!busy}
      footer={
        <>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void run('skip', onSkip)}
            className="rounded-full border border-black/10 px-4 py-2 text-sm font-medium text-black transition hover:bg-black/5 disabled:opacity-50"
          >
            {busy === 'skip' ? '启动中…' : '不使用 Wiki 运行'}
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void run('allow', onAllow)}
            className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white transition hover:bg-black/85 disabled:opacity-50"
          >
            {busy === 'allow' ? '授权中…' : '允许并记住'}
          </button>
        </>
      }
    >
      <div className="space-y-3 rounded-2xl border border-black/[0.07] bg-[#f7f6f2] p-4 text-sm text-black/65">
        <div className="flex gap-3">
          <BookOpen className="mt-0.5 h-4 w-4 shrink-0 text-black/55" />
          <p className="leading-6">Wiki 只读挂载，应用输出和生成文件不会自动写回。</p>
        </div>
        <div className="flex gap-3">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-black/55" />
          <p className="leading-6">授权与当前工作流版本绑定；发布者修改工作流后会再次询问。</p>
        </div>
      </div>
      {error ? <div className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div> : null}
    </AppDialog>
  );
}
