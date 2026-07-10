// Shared shell for the simple node cards. Handles selection state, the colored
// header bar, title/subtitle, and the small action icon row.

import type { ReactNode } from 'react';
import { Handle, Position } from '@xyflow/react';

const HEADER_HANDLE_TOP_PX = 14;

export interface NodeShellProps {
  id: string;
  selected: boolean;
  bg: string; // tailwind bg-* class (header tint)
  icon: ReactNode;
  title: string;
  subtitle?: string;
  badge?: ReactNode;
  showSourceHandle?: boolean;
  showTargetHandle?: boolean;
  children?: ReactNode; // optional content under the subtitle (e.g. asset thumbnails)
}

export function NodeShell({
  id,
  selected,
  bg,
  icon,
  title,
  subtitle,
  badge,
  showSourceHandle = true,
  showTargetHandle = true,
  children,
}: NodeShellProps) {
  return (
    <div className={`mira-node ${selected ? 'selected' : ''}`} data-node-id={id}>
      {showTargetHandle && (
        <Handle
          id="target"
          type="target"
          position={Position.Left}
          isConnectableStart={false}
          className="mira-node-target-handle"
          style={{ top: HEADER_HANDLE_TOP_PX }}
        />
      )}
      <div className={`flex items-center gap-2 ${bg} -mx-3.5 -mt-3 px-3 py-1.5 rounded-t-[13px] text-[12px] font-medium`}>
        <span className="opacity-80">{icon}</span>
        <span className="truncate">{title}</span>
        <span className="ml-auto opacity-60">{badge}</span>
      </div>
      {subtitle && (
        <div className="mt-2 text-[11px] text-black/55 line-clamp-2">{subtitle}</div>
      )}
      {children}
      {showSourceHandle && (
        <Handle
          id="source"
          type="source"
          position={Position.Right}
          isConnectableEnd={false}
          className="mira-node-source-handle"
          style={{ top: HEADER_HANDLE_TOP_PX }}
        />
      )}
    </div>
  );
}
