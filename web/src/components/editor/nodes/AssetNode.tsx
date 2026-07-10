import { memo, useEffect, useState } from 'react';
import type { NodeProps } from '@xyflow/react';
import { NodeShell } from './NodeCard';
import { BrushIcon, FileIcon, LinkIcon, TextIcon } from '../../common/Icons';
import { fetchUploadBlob } from '../../../lib/api';
import type { AssetNode as TAsset } from '../../../types';

function previewFor(node: TAsset): string {
  switch (node.asset_kind) {
    case 'text':
      return node.content ? node.content.slice(0, 60) : '空';
    case 'url':
      return node.urls.length ? `${node.urls.length} 个链接 · ${node.urls[0]}` : '暂无 URL';
    case 'file':
      return node.uploads.length ? `${node.uploads.length} 个文件 · ${node.uploads[0]?.name ?? ''}` : '尚未选择文件';
    case 'drawing':
      return node.upload ? '已保存的画作' : '空';
    default:
      return '';
  }
}

function iconFor(kind: TAsset['asset_kind']) {
  switch (kind) {
    case 'text': return <TextIcon className="w-3.5 h-3.5" />;
    case 'url': return <LinkIcon className="w-3.5 h-3.5" />;
    case 'file': return <FileIcon className="w-3.5 h-3.5" />;
    case 'drawing': return <BrushIcon className="w-3.5 h-3.5" />;
    default: return <TextIcon className="w-3.5 h-3.5" />;
  }
}

function useThumbnail(node: TAsset): string {
  const [url, setUrl] = useState('');
  const imageUpload =
    node.asset_kind === 'file'
      ? node.uploads.find((upload) => upload.mime.startsWith('image/')) ?? null
      : node.asset_kind === 'drawing'
        ? node.upload
        : null;

  useEffect(() => {
    if (!imageUpload || !imageUpload.mime.startsWith('image/')) {
      setUrl('');
      return;
    }
    const controller = new AbortController();
    let objectUrl = '';
    fetchUploadBlob(imageUpload.id, controller.signal)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setUrl('');
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [imageUpload?.id, imageUpload?.mime]);

  return url;
}

export const AssetNodeView = memo(function AssetNodeView({ id, data, selected }: NodeProps) {
  const node = data as unknown as TAsset;
  const preview = previewFor(node);
  const thumbnail = useThumbnail(node);

  return (
    <NodeShell
      id={id}
      selected={!!selected}
      bg="bg-nodePurple"
      icon={iconFor(node.asset_kind)}
      title={node.title || '素材'}
      subtitle={preview || undefined}
      showTargetHandle={false}
    >
      {thumbnail && (
        <img
          src={thumbnail}
          alt={node.title || '素材'}
          className="mt-2 max-h-28 w-full object-contain rounded border border-black/5 bg-white"
        />
      )}
    </NodeShell>
  );
});
