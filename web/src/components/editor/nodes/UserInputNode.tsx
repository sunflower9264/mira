import { memo } from 'react';
import type { NodeProps } from '@xyflow/react';
import { NodeShell } from './NodeCard';
import { InputIcon } from '../../common/Icons';
import type { UserInputNode as TUserInput } from '../../../types';

export const UserInputNodeView = memo(function UserInputNodeView({ id, data, selected }: NodeProps) {
  const node = data as unknown as TUserInput;
  return (
    <NodeShell
      id={id}
      selected={!!selected}
      bg="bg-nodeYellow"
      icon={<InputIcon className="w-3.5 h-3.5" />}
      title={node.title || '用户输入'}
      subtitle={node.description || node.input_schema?.label}
      showTargetHandle={false}
    />
  );
});
