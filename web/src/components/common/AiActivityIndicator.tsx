import type { AiActivity } from '../../types';

export function AiActivityIndicator({
  status,
  label,
  compact = false,
}: {
  status: AiActivity['status'];
  label?: string;
  compact?: boolean;
}) {
  const waiting = status === 'waiting_user';
  return (
    <span
      className="inline-flex min-w-0 items-center gap-2"
      role="status"
      aria-live="polite"
      aria-busy={!waiting}
    >
      {waiting ? (
        <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-amber-500" aria-hidden="true" />
      ) : (
        <span className="flex shrink-0 items-center gap-1" aria-hidden="true">
          {[0, 1, 2].map((index) => (
            <span
              key={index}
              className={`${compact ? 'h-1.5 w-1.5' : 'h-2 w-2'} rounded-full bg-black/70 motion-safe:animate-pulse motion-reduce:animate-none`}
              style={{ animationDelay: `${index * 180}ms` }}
            />
          ))}
        </span>
      )}
      {label ? <span className="truncate">{label}</span> : <span className="sr-only">{waiting ? '等待你回复' : 'AI 正在处理'}</span>}
    </span>
  );
}
