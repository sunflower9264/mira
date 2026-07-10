// 编辑应用名称与描述的弹窗。

import { useEffect, useRef, useState } from 'react';
import { AppDialog } from './AppDialog';
import { uploadFile } from '../../lib/api';
import { isAppCoverUploadId, useAppCoverUrl } from '../../hooks/useAppCoverUrl';
import { showCaughtError } from '../../stores/useErrorDialogStore';

const COVER_MAX_BYTES = 2 * 1024 * 1024; // 2MB
const COVER_ACCEPT = 'image/png,image/jpeg,image/webp,image/gif';

interface EditAppDialogProps {
  open: boolean;
  onClose(): void;
  initialName: string;
  initialDescription: string;
  initialCover: string | null;
  appId: string;
  onSave(values: {
    name: string;
    description: string;
    cover: string | null;
  }): void | Promise<void>;
}

export function EditAppDialog({
  open,
  onClose,
  initialName,
  initialDescription,
  initialCover,
  appId,
  onSave,
}: EditAppDialogProps) {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription);
  const [cover, setCover] = useState<string | null>(initialCover);
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [localCoverUrl, setLocalCoverUrl] = useState<string | null>(null);
  const [coverError, setCoverError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const savedCoverUrl = useAppCoverUrl({ id: appId, cover });
  const coverUrl = localCoverUrl ?? savedCoverUrl;

  useEffect(() => {
    if (!open) return;
    setName(initialName);
    setDescription(initialDescription);
    setCover(isAppCoverUploadId(initialCover) ? initialCover : null);
    setCoverFile(null);
    setLocalCoverUrl(null);
    setCoverError(null);
    setBusy(false);
    const timer = window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [open, initialName, initialDescription, initialCover]);

  useEffect(() => {
    return () => {
      if (localCoverUrl) URL.revokeObjectURL(localCoverUrl);
    };
  }, [localCoverUrl]);

  const handlePickCover = () => {
    fileRef.current?.click();
  };

  const handleCoverChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setCoverError('请选择图片文件');
      return;
    }
    if (file.size > COVER_MAX_BYTES) {
      setCoverError('图片过大，请选择小于 2MB 的图片');
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    if (localCoverUrl) URL.revokeObjectURL(localCoverUrl);
    setCoverFile(file);
    setLocalCoverUrl(objectUrl);
    setCover(null);
    setCoverError(null);
  };

  const handleRemoveCover = () => {
    if (localCoverUrl) URL.revokeObjectURL(localCoverUrl);
    setLocalCoverUrl(null);
    setCoverFile(null);
    setCover(null);
    setCoverError(null);
  };

  const trimmed = name.trim();
  const disabled = trimmed.length === 0;

  const handleSave = async () => {
    if (disabled || busy) return;
    setBusy(true);
    try {
      let nextCover = cover;
      if (coverFile) {
        const uploaded = await uploadFile(coverFile);
        nextCover = uploaded.id;
      }
      await onSave({ name: trimmed, description, cover: nextCover });
      onClose();
    } catch (error) {
      showCaughtError(error, '保存失败', '保存失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppDialog
      open={open}
      onClose={busy ? () => undefined : onClose}
      title="编辑应用"
      description="修改应用的名称和描述。"
      footer={
        <>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-full border border-black/10 px-4 py-2 text-sm font-medium text-black transition hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={busy || disabled}
            className="rounded-full bg-black px-4 py-2 text-sm font-medium text-white transition hover:bg-black/85 disabled:cursor-not-allowed disabled:opacity-50"
          >
            保存
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <label className="block">
          <span className="block text-[11px] uppercase tracking-wider text-black/55">
            应用名称
          </span>
          <input
            ref={inputRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void handleSave();
              }
            }}
            placeholder="请输入应用名称"
            className="mt-2 w-full rounded-xl border border-black/10 px-3 py-2.5 text-sm outline-none transition focus:border-black/30"
          />
        </label>
        <label className="block">
          <span className="block text-[11px] uppercase tracking-wider text-black/55">
            应用描述
          </span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="一句话介绍这个应用（可选）"
            rows={5}
            className="mt-2 w-full resize-y rounded-xl border border-black/10 px-3 py-2.5 text-sm leading-6 outline-none transition focus:border-black/30"
          />
        </label>
        <div>
          <span className="block text-[11px] uppercase tracking-wider text-black/55">
            封面图
          </span>
          <input
            ref={fileRef}
            type="file"
            accept={COVER_ACCEPT}
            onChange={handleCoverChange}
            className="hidden"
          />
          <div className="mt-2">
            {coverUrl ? (
              <div className="group relative overflow-hidden rounded-2xl border border-black/10 bg-black/[0.02]">
                <div
                  className="aspect-video w-full bg-cover bg-center"
                  style={{ backgroundImage: `url(${coverUrl})` }}
                  aria-label="封面预览"
                />
                <div className="absolute right-2 top-2 flex gap-2">
                  <button
                    type="button"
                    onClick={handlePickCover}
                    className="rounded-full bg-white/90 px-3 py-1 text-xs font-medium text-black shadow-sm backdrop-blur transition hover:bg-white"
                  >
                    替换
                  </button>
                  <button
                    type="button"
                    onClick={handleRemoveCover}
                    className="rounded-full bg-white/90 px-3 py-1 text-xs font-medium text-red-600 shadow-sm backdrop-blur transition hover:bg-white"
                  >
                    移除
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={handlePickCover}
                className="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-2xl border border-dashed border-black/15 bg-black/[0.02] text-sm text-black/55 transition hover:bg-black/[0.04]"
              >
                <span className="text-base">+</span>
                <span>点击上传图片</span>
                <span className="text-[11px] text-black/40">PNG / JPG / WEBP / GIF，≤2MB</span>
              </button>
            )}
          </div>
          {coverError ? (
            <div className="mt-2 text-xs text-red-600">{coverError}</div>
          ) : null}
        </div>
      </div>
    </AppDialog>
  );
}
