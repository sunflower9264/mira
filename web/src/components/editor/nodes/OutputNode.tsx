import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { NodeShell } from './NodeCard';
import { OutputIcon } from '../../common/Icons';
import type { OutputNode as TOutput } from '../../../types';

export const OutputNodeView = memo(function OutputNodeView({ id, data, selected }: NodeProps) {
  const node = data as unknown as TOutput;
  const missingConfig = !node.prompt?.trim();
  const promptPreview = node.prompt?.trim();
  const subtitle = promptPreview ? `${promptPreview.slice(0, 60)}…` : node.description || '填写输出提示词';
  return (
    <NodeShell
      id={id}
      selected={!!selected}
      bg="bg-nodeGreen"
      icon={<OutputIcon className="w-3.5 h-3.5" />}
      title={node.title || '输出'}
      subtitle={subtitle}
      badge={<span className="text-[10px] uppercase tracking-wider">{missingConfig ? '未配置' : '输出'}</span>}
      showSourceHandle={false}
    />
  );
});
