import { useEffect, useState } from 'react';
import { Download, FileText } from 'lucide-react';
import { listRunArtifacts } from '../../lib/api';
import { showCaughtError } from '../../stores/useErrorDialogStore';
import type { RunArtifact } from '../../types';

interface RunArtifactsPanelProps {
  runId: string | null;
  className?: string;
  density?: 'desktop' | 'mobile';
}

export function RunArtifactsPanel({
  runId,
  className = '',
  density = 'desktop',
}: RunArtifactsPanelProps) {
  const [artifacts, setArtifacts] = useState<RunArtifact[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) {
      setArtifacts([]);
      setTruncated(false);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setArtifacts([]);
    setTruncated(false);
    void listRunArtifacts(runId)
      .then((payload) => {
        if (cancelled) return;
        setArtifacts(payload.artifacts);
        setTruncated(payload.truncated);
      })
      .catch((err) => {
        if (cancelled) return;
        setArtifacts([]);
        setTruncated(false);
        setError(null);
        showCaughtError(err, '文件产物加载失败', '加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (!runId || (!loading && !error && artifacts.length === 0)) return null;

  const mobile = density === 'mobile';

  return (
    <section
      className={[
        mobile ? 'rounded-[22px] p-3' : 'rounded-2xl p-4',
        'border border-black/10 bg-white shadow-card',
        className,
      ].filter(Boolean).join(' ')}
    >
      <header className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-black/[0.04] text-black/65">
            <FileText className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <h3 className={mobile ? 'text-sm font-semibold' : 'text-base font-semibold'}>文件产物</h3>
            {artifacts.length ? (
              <div className="mt-0.5 text-xs text-black/45">
                {artifacts.length} 个文件{truncated ? ' · 已截断' : ''}
              </div>
            ) : null}
          </div>
        </div>
      </header>

      {loading ? (
        <div className="mt-3 rounded-xl bg-black/[0.03] px-3 py-2 text-sm text-black/45">加载中...</div>
      ) : error ? (
        <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      ) : (
        <ul className="mt-3 divide-y divide-black/5 overflow-hidden rounded-xl border border-black/10">
          {artifacts.map((artifact) => (
            <li key={artifact.id} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-black/78">{artifact.name}</div>
                <div className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-black/42">
                  {artifact.size !== null && artifact.size !== undefined ? (
                    <span className="font-mono">{formatBytes(artifact.size)}</span>
                  ) : null}
                  {artifact.origin_node_title ? <span className="truncate">{artifact.origin_node_title}</span> : null}
                </div>
              </div>
              <a
                href={artifact.download_url}
                download
                className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-black/10 text-black/65 hover:border-black/20 hover:bg-black/[0.03] hover:text-black"
                aria-label={`下载 ${artifact.name}`}
                title="下载"
              >
                <Download className="h-4 w-4" />
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return '0 B';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
