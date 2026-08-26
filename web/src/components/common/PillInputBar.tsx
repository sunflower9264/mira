// 公共药丸输入框：textarea 自动撑开高度，限制最大高度（超出滚动），
// 多行时圆角降级为 rounded-3xl 以避免 rounded-full 在大块文本下变形。
// 通过 topSlot 在输入框上方挂可选内容（如运行流提示）。
// 可选 allowAttachments：选中后立即 POST /api/uploads；图片在输入框上方显示缩略图，
// 上传中带灰色蒙层和转圈，完成后露出原图；非图片仍用文件名 chip。

import {
  type ChangeEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { uploadFile } from '../../lib/api';
import { SendIcon, StopIcon } from './Icons';

const SCROLLBAR_CLASSES =
  '[&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-button]:hidden [&::-webkit-scrollbar-button]:h-0 [&::-webkit-scrollbar-button]:w-0 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-black/20';

const DEFAULT_MAX_HEIGHT = 200;
// 单行 textarea 的近似 scrollHeight：line-height 24 + 余量。超过则视为多行。
const MULTILINE_THRESHOLD = 32;

const DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const DEFAULT_MAX_ATTACHMENTS = 5;

export type PillAttachmentStatus = 'uploading' | 'ready' | 'error';

export interface PillAttachment {
  /** 本地稳定 id，仅用于 chip 渲染时的 key（不是后端 upload id）。*/
  id: string;
  name: string;
  size: number;
  /** 客户端保留的 File 引用，选中后立即用来调 POST /api/uploads。*/
  file?: File;
  /** 后端 POST /api/uploads 返回的 upload id。*/
  uploadId?: string;
  /** 兼容字段：早期版本用 dataURL 直传。新代码不再读取，但保留向后兼容。*/
  dataUrl?: string;
  mimeType?: string;
  status?: PillAttachmentStatus;
  error?: string;
  /** 图片本地预览 object URL，由 PillInputBar 创建并在移除时释放。*/
  previewUrl?: string;
}

export interface PillInputBarProps {
  value: string;
  onChange(value: string): void;
  onSubmit(): void;
  onCancel?(): void;
  placeholder?: string;
  canSubmit?: boolean;
  submitting?: boolean;
  maxHeight?: number;
  topSlot?: ReactNode;
  ariaLabel?: string;
  /** 启用左侧上传按钮；图片缩略图、非图片 chip 显示在输入框上方。需配合 attachments + onAttachmentsChange。 */
  allowAttachments?: boolean;
  attachments?: PillAttachment[];
  onAttachmentsChange?(next: PillAttachment[]): void;
  /** 按附件 id 回写上传结果。决策多题切换时必须提供，避免写到当前正在显示的那一题。 */
  onAttachmentUpdate?(id: string, patch: Partial<PillAttachment>): void;
  acceptAttachments?: string;
  maxAttachmentBytes?: number;
  maxAttachmentCount?: number;
  /** 隐藏右侧发送按钮（提交逻辑由父级承担）。textarea 的 Enter 仍会触发 onSubmit。 */
  hideSubmit?: boolean;
  /** 禁止自由文本输入，但保留发送按钮用于提交父级已有状态。 */
  readOnly?: boolean;
}

export function PillInputBar({
  value,
  onChange,
  onSubmit,
  onCancel,
  placeholder,
  canSubmit = true,
  submitting = false,
  maxHeight = DEFAULT_MAX_HEIGHT,
  topSlot,
  ariaLabel = '发送',
  allowAttachments = false,
  attachments,
  onAttachmentsChange,
  onAttachmentUpdate,
  acceptAttachments,
  maxAttachmentBytes = DEFAULT_MAX_ATTACHMENT_BYTES,
  maxAttachmentCount = DEFAULT_MAX_ATTACHMENTS,
  hideSubmit = false,
  readOnly = false,
}: PillInputBarProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<PillAttachment[]>([]);
  const uploadsRef = useRef(new Map<string, AbortController>());
  const onAttachmentsChangeRef = useRef(onAttachmentsChange);
  const onAttachmentUpdateRef = useRef(onAttachmentUpdate);
  const [overflowing, setOverflowing] = useState(false);
  const [multiline, setMultiline] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);

  const list = attachments ?? [];
  listRef.current = list;
  onAttachmentsChangeRef.current = onAttachmentsChange;
  onAttachmentUpdateRef.current = onAttachmentUpdate;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    const needed = el.scrollHeight;
    el.style.height = `${Math.min(needed, maxHeight)}px`;
    setOverflowing(needed > maxHeight);
    setMultiline(needed > MULTILINE_THRESHOLD);
  }, [value, maxHeight]);

  useEffect(() => () => {
    for (const controller of uploadsRef.current.values()) controller.abort();
    uploadsRef.current.clear();
  }, []);

  const attachmentsBusy = hasPendingAttachments(list);
  const enabled = canSubmit && !submitting && !attachmentsBusy;
  const canCancel = submitting && typeof onCancel === 'function';
  const attachmentsEnabled = allowAttachments && typeof onAttachmentsChange === 'function';
  const imageItems = list.filter(isImageAttachment);
  const fileItems = list.filter((item) => !isImageAttachment(item));

  const patchAttachment = (id: string, patch: Partial<PillAttachment>) => {
    if (onAttachmentUpdateRef.current) {
      onAttachmentUpdateRef.current(id, patch);
      return;
    }
    const next = listRef.current.map((item) => (item.id === id ? { ...item, ...patch } : item));
    listRef.current = next;
    onAttachmentsChangeRef.current?.(next);
  };

  const startUpload = (item: PillAttachment) => {
    if (!item.file || item.uploadId) return;
    uploadsRef.current.get(item.id)?.abort();
    const controller = new AbortController();
    uploadsRef.current.set(item.id, controller);
    void uploadFile(item.file, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        patchAttachment(item.id, { uploadId: result.id, status: 'ready', error: undefined });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        patchAttachment(item.id, {
          status: 'error',
          error: error instanceof Error ? error.message : '上传失败',
        });
      })
      .finally(() => {
        if (uploadsRef.current.get(item.id) === controller) uploadsRef.current.delete(item.id);
      });
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    setAttachError(null);
    const files = Array.from(event.target.files ?? []);
    event.target.value = '';
    if (!files.length || !onAttachmentsChange) return;
    if (list.length + files.length > maxAttachmentCount) {
      setAttachError(`最多只能附加 ${maxAttachmentCount} 个文件`);
      return;
    }
    const oversized = files.find((f) => f.size > maxAttachmentBytes);
    if (oversized) {
      setAttachError(`文件「${oversized.name}」过大（限制 ${(maxAttachmentBytes / 1024 / 1024).toFixed(0)}MB）`);
      return;
    }
    const nextItems = files.map(toAttachment);
    const next = [...list, ...nextItems];
    listRef.current = next;
    onAttachmentsChange(next);
    for (const item of nextItems) startUpload(item);
  };

  const removeAttachment = (id: string) => {
    if (!onAttachmentsChange) return;
    const target = list.find((item) => item.id === id);
    uploadsRef.current.get(id)?.abort();
    uploadsRef.current.delete(id);
    if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
    onAttachmentsChange(list.filter((item) => item.id !== id));
  };

  const retryAttachment = (item: PillAttachment) => {
    if (!item.file || submitting) return;
    patchAttachment(item.id, { status: 'uploading', error: undefined });
    startUpload(item);
  };

  return (
    <div>
      {topSlot ? <div className="mb-2">{topSlot}</div> : null}
      {attachmentsEnabled && list.length > 0 ? (
        <div className="mb-2 flex flex-wrap items-end gap-1.5">
          {imageItems.map((att) => (
            <ImageThumb
              key={att.id}
              attachment={att}
              disabled={submitting}
              onRemove={() => removeAttachment(att.id)}
              onRetry={() => retryAttachment(att)}
            />
          ))}
          {fileItems.map((att) => (
            <FileChip
              key={att.id}
              attachment={att}
              disabled={submitting}
              onRemove={() => removeAttachment(att.id)}
              onRetry={() => retryAttachment(att)}
            />
          ))}
        </div>
      ) : null}
      {attachError ? (
        <div className="mb-2 text-xs text-red-600">{attachError}</div>
      ) : null}
      <div
        className={`flex gap-2 bg-white border border-black/10 shadow-pill px-3 py-2 ${
          multiline ? 'items-end rounded-3xl' : 'items-center rounded-full'
        }`}
      >
        {attachmentsEnabled ? (
          <>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={submitting || list.length >= maxAttachmentCount}
              aria-label="上传文件"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-black/55 transition hover:bg-black/[0.05] hover:text-black/85 disabled:cursor-not-allowed disabled:text-black/25 disabled:hover:bg-transparent"
            >
              <PaperclipIcon className="h-4 w-4" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={acceptAttachments}
              className="hidden"
              onChange={handleFileChange}
            />
          </>
        ) : null}
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder={placeholder}
          readOnly={readOnly}
          onChange={(event) => {
            if (!readOnly) onChange(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (enabled) onSubmit();
            }
          }}
          className={`flex-1 min-w-0 bg-transparent outline-none text-sm leading-6 placeholder:text-black/40 resize-none ${
            overflowing ? 'overflow-x-hidden overflow-y-auto' : 'overflow-hidden'
          } ${SCROLLBAR_CLASSES}`}
          style={{ maxHeight, padding: 0 }}
        />
        {hideSubmit ? null : (
          <button
            type="button"
            onClick={canCancel ? onCancel : onSubmit}
            disabled={!enabled && !canCancel}
            aria-label={canCancel ? '中止' : ariaLabel}
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-white transition ${
              canCancel
                ? 'bg-red-600 hover:bg-red-700 disabled:bg-red-600/40'
                : 'bg-black hover:bg-black/85 disabled:bg-black/30'
            }`}
          >
            {canCancel ? (
              <StopIcon className="h-3.5 w-3.5" />
            ) : submitting ? (
              <span className="block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            ) : (
              <SendIcon className="w-4 h-4" />
            )}
          </button>
        )}
      </div>
    </div>
  );
}

const IMAGE_NAME = /\.(png|jpe?g|gif|webp|bmp)$/i;

export function isImageAttachment(item: Pick<PillAttachment, 'mimeType' | 'name' | 'file'>): boolean {
  const mime = item.mimeType || item.file?.type || '';
  if (mime.startsWith('image/')) return true;
  return IMAGE_NAME.test(item.name);
}

export function patchPillAttachment(
  attachments: PillAttachment[],
  id: string,
  patch: Partial<PillAttachment>,
): PillAttachment[] {
  return attachments.map((item) => (item.id === id ? { ...item, ...patch } : item));
}

export function patchDraftAttachment<T extends { attachments: PillAttachment[] }>(
  drafts: Record<string, T>,
  id: string,
  patch: Partial<PillAttachment>,
): Record<string, T> {
  let changed = false;
  const next: Record<string, T> = {};
  for (const [key, draft] of Object.entries(drafts)) {
    if (!draft.attachments.some((item) => item.id === id)) {
      next[key] = draft;
      continue;
    }
    changed = true;
    next[key] = { ...draft, attachments: patchPillAttachment(draft.attachments, id, patch) };
  }
  return changed ? next : drafts;
}

export function hasPendingAttachments(items: PillAttachment[]): boolean {
  return items.some((item) => item.status === 'uploading' || item.status === 'error' || (!item.uploadId && !!item.file));
}

export function hasPendingDraftAttachments(drafts: Record<string, { attachments: PillAttachment[] }>): boolean {
  return Object.values(drafts).some((draft) => hasPendingAttachments(draft.attachments));
}

function toAttachment(file: File): PillAttachment {
  const mimeType = file.type || undefined;
  const image = isImageAttachment({ mimeType, name: file.name, file });
  return {
    id: `att_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    name: file.name,
    size: file.size,
    file,
    mimeType,
    status: 'uploading',
    previewUrl: image ? URL.createObjectURL(file) : undefined,
  };
}

function isAbortError(error: unknown): boolean {
  return (error instanceof DOMException || error instanceof Error) && error.name === 'AbortError';
}

function ImageThumb({
  attachment,
  disabled,
  onRemove,
  onRetry,
}: {
  attachment: PillAttachment;
  disabled: boolean;
  onRemove(): void;
  onRetry(): void;
}) {
  const uploading = attachment.status === 'uploading' || (!attachment.uploadId && attachment.status !== 'error');
  const failed = attachment.status === 'error';
  return (
    <div className="relative h-12 w-12 shrink-0" title={attachment.name}>
      <div className="relative h-12 w-12 overflow-hidden rounded-xl border border-black/10 bg-black/[0.04]">
        {attachment.previewUrl ? (
          <img src={attachment.previewUrl} alt={attachment.name} className="h-full w-full object-cover" />
        ) : (
          <div className="grid h-full w-full place-items-center text-[10px] text-black/35">图</div>
        )}
        {uploading ? (
          <div className="absolute inset-0 grid place-items-center bg-black/45" aria-label={`${attachment.name} 正在上传`}>
            <span className="block h-4 w-4 animate-spin rounded-full border-2 border-white/35 border-t-white" />
          </div>
        ) : failed ? (
          <button
            type="button"
            onClick={onRetry}
            disabled={disabled}
            className="absolute inset-0 grid place-items-center bg-black/45"
            aria-label={`重试上传 ${attachment.name}`}
          >
            <span className="px-1 text-center text-[10px] leading-3 text-white">失败</span>
          </button>
        ) : null}
      </div>
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        className="absolute -right-1 -top-1 grid h-4 w-4 place-items-center rounded-full border border-black/10 bg-white text-black/55 shadow-sm hover:text-black disabled:cursor-not-allowed disabled:text-black/25"
        aria-label={`移除 ${attachment.name}`}
      >
        <CloseIcon className="h-2.5 w-2.5" />
      </button>
    </div>
  );
}

function FileChip({
  attachment,
  disabled,
  onRemove,
  onRetry,
}: {
  attachment: PillAttachment;
  disabled: boolean;
  onRemove(): void;
  onRetry(): void;
}) {
  const uploading = attachment.status === 'uploading' || (!attachment.uploadId && attachment.status !== 'error');
  const failed = attachment.status === 'error';
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs shadow-pill ${
        failed ? 'border-red-200 bg-red-50 text-red-700' : 'border-black/10 bg-white text-black/75'
      }`}
      title={failed ? attachment.error || '上传失败' : `${attachment.name} · ${formatSize(attachment.size)}`}
    >
      {uploading ? (
        <span className="block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-black/15 border-t-black/60" />
      ) : (
        <PaperclipIcon className="h-3 w-3 shrink-0 text-black/55" />
      )}
      <span className="max-w-[160px] truncate">{attachment.name}</span>
      {failed ? (
        <button type="button" onClick={onRetry} disabled={disabled} className="text-[10px] font-medium underline underline-offset-2">
          重试
        </button>
      ) : null}
      <button
        type="button"
        onClick={onRemove}
        disabled={disabled}
        className="ml-0.5 grid h-4 w-4 place-items-center rounded-full text-black/45 hover:bg-black/[0.06] hover:text-black/85 disabled:cursor-not-allowed disabled:text-black/25 disabled:hover:bg-transparent"
        aria-label={`移除 ${attachment.name}`}
      >
        <CloseIcon className="h-2.5 w-2.5" />
      </button>
    </span>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function PaperclipIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </svg>
  );
}

function CloseIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}
