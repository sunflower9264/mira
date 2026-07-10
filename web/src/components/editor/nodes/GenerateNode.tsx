import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { NodeShell } from './NodeCard';
import { SparkleIcon } from '../../common/Icons';
import type { GenerateNode as TGenerate } from '../../../types';

export const GenerateNodeView = memo(function GenerateNodeView({ id, data, selected }: NodeProps) {
  const node = data as unknown as TGenerate;
  const missingConfig = !node.prompt?.trim();
  const promptPreview = node.prompt?.trim();
  const sub = promptPreview ? `${promptPreview.slice(0, 60)}…` : node.description || '描述这个步骤';
  return (
    <NodeShell
      id={id}
      selected={!!selected}
      bg="bg-nodeBlue"
      icon={<SparkleIcon className="w-3.5 h-3.5" />}
      title={node.title || '生成'}
      subtitle={sub}
      badge={<span className="text-[10px] uppercase tracking-wider">{missingConfig ? '未配置' : '生成'}</span>}
    />
  );
});
