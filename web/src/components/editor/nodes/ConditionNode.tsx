// Condition 节点视图：右侧渲染 N 个 source handle，每个分支一个独立接口。
// cases 模式额外追加一个 `__default__` handle（标"其它"，灰显）作为隐式兜底；
// binary 模式只展示 true/false 两个 handle（false 即兜底）。

import { memo, useEffect, useMemo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { Handle, Position, useUpdateNodeInternals } from '@xyflow/react';
import { BranchIcon } from '../../common/Icons';
import {
  CONDITION_DEFAULT_BRANCH_KEY,
  type ConditionNode as TCondition,
} from '../../../types';

interface RenderBranch {
  key: string;
  label: string;
  isDefault: boolean;
}

const HEADER_HANDLE_TOP_PX = 14;

function buildBranches(node: TCondition): RenderBranch[] {
  if (node.mode === 'binary') {
    return [
      { key: 'true', label: 'true', isDefault: false },
      { key: 'false', label: 'false', isDefault: false },
    ];
  }
  const userBranches: RenderBranch[] = (node.branches ?? [])
    .filter((branch) => branch && typeof branch.key === 'string' && branch.key && branch.key !== CONDITION_DEFAULT_BRANCH_KEY)
    .map((branch) => ({
      key: branch.key,
      label: (branch.label && branch.label.trim()) || branch.key,
      isDefault: false,
    }));
  return [
    ...userBranches,
    { key: CONDITION_DEFAULT_BRANCH_KEY, label: '其它', isDefault: true },
  ];
}

export const ConditionNodeView = memo(function ConditionNodeView({ id, data, selected }: NodeProps) {
  const node = data as unknown as TCondition;
  const branches = useMemo(() => buildBranches(node), [node]);
  const branchKeySignature = branches.map((branch) => branch.key).join('\u0000');
  const updateNodeInternals = useUpdateNodeInternals();
  const promptPreview = node.prompt?.trim();
  const subtitle = promptPreview ? `${promptPreview.slice(0, 60)}…` : node.description || '描述判断条件';

  // 把 source handle 直接放在 mira-node 这一级（绝对定位以 .react-flow__handle.right 为准），
  // 用 inline style 的 top 把每个分支均匀分布在节点右侧。这样 React Flow 计算连线
  // 起点时拿到的是相对于节点的坐标，与单 handle 节点行为一致。
  const branchAreaStartPx = 76; // header(约 28) + subtitle(约 36) + 上间距
  const branchRowPx = 26;

  useEffect(() => {
    updateNodeInternals(id);
  }, [branchKeySignature, id, updateNodeInternals]);

  return (
    <div className={`mira-node ${selected ? 'selected' : ''} pb-2`} data-node-id={id}>
      <Handle
        id="target"
        type="target"
        position={Position.Left}
        isConnectableStart={false}
        className="mira-node-target-handle"
        style={{ top: HEADER_HANDLE_TOP_PX }}
      />
      <div className="flex items-center gap-2 bg-nodeOrange -mx-3.5 -mt-3 px-3 py-1.5 rounded-t-[13px] text-[12px] font-medium">
        <span className="opacity-80"><BranchIcon className="w-3.5 h-3.5" /></span>
        <span className="truncate">{node.title || '判断'}</span>
        <span className="ml-auto opacity-60 text-[10px] uppercase tracking-wider">
          {node.mode === 'binary' ? 'binary' : 'cases'}
        </span>
      </div>
      <div className="mt-2 text-[11px] text-black/55 line-clamp-2">{subtitle}</div>
      <ul className="mt-2 flex flex-col gap-1.5 text-[11px]">
        {branches.map((branch) => (
          <li
            key={branch.key}
            className={`flex items-center justify-between rounded-md px-2 py-0.5 ${
              branch.isDefault ? 'bg-black/[0.03] text-black/45' : 'bg-black/[0.04] text-black/70'
            }`}
          >
            <span className="truncate pr-2">{branch.label}</span>
            <span className="text-[10px] opacity-60">→</span>
          </li>
        ))}
      </ul>
      {branches.map((branch, index) => (
        <Handle
          key={branch.key}
          id={branch.key}
          type="source"
          position={Position.Right}
          isConnectableEnd={false}
          className="mira-node-source-handle"
          style={{ top: branchAreaStartPx + index * branchRowPx }}
        />
      ))}
    </div>
  );
});
