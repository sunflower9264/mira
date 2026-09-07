import type { ReactNode } from 'react';
import { AiActivityIndicator } from './AiActivityIndicator';

export function LoadingOverlay({
  show,
  message,
  className = '',
}: {
  show: boolean;
  message: ReactNode;
  className?: string;
}) {
  if (!show) return null;
  return (
    <div className={`absolute inset-0 z-10 grid place-items-center bg-white/75 backdrop-blur-[1px] ${className}`}>
      <div className="flex items-center gap-2 rounded-full border border-black/10 bg-white/90 px-3 py-2 text-xs font-medium text-black/65 shadow-pill">
        <AiActivityIndicator status="working" label={typeof message === 'string' ? message : undefined} compact />
        {typeof message === 'string' ? null : <span>{message}</span>}
      </div>
    </div>
  );
}
