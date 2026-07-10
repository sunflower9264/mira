// 公共药丸输入框：textarea 自动撑开高度，限制最大高度（超出滚动），
// 多行时圆角降级为 rounded-3xl 以避免 rounded-full 在大块文本下变形。
// 通过 topSlot 在输入框上方挂可选内容（如运行流提示）。
// 可选 allowAttachments：左侧显示上传按钮，已添加的文件以 chips 形式显示在 pill 上方。

import {
  type ChangeEvent,
  type ReactNode,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { SendIcon, StopIcon } from './Icons';

const SCROLLBAR_CLASSES =
  '[&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-button]:hidden [&::-webkit-scrollbar-button]:h-0 [&::-webkit-scrollbar-button]:w-0 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-black/20';

const DEFAULT_MAX_HEIGHT = 200;
// 单行 textarea 的近似 scrollHeight：line-height 24 + 余量。超过则视为多行。
const MULTILINE_THRESHOLD = 32;

const DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
const DEFAULT_MAX_ATTACHMENTS = 5;

export interface PillAttachment {
  /** 本地稳定 id，仅用于 chip 渲染时的 key（不是后端 upload id）。*/
  id: string;
  name: string;
  size: number;
  /** 客户端保留的 File 引用，提交时用来调 POST /api/uploads。*/
  file?: File;
  /** 后端 POST /api/uploads 返回的 upload id；提交后由父组件回填。*/
  uploadId?: string;
  /** 兼容字段：早期版本用 dataURL 直传。新代码不再读取，但保留向后兼容。*/
  dataUrl?: string;
  mimeType?: string;
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
  /** 启用左侧上传按钮 + 附件 chips。需配合 attachments + onAttachmentsChange。 */
  allowAttachments?: boolean;
  attachments?: PillAttachment[];
  onAttachmentsChange?(next: PillAttachment[]): void;
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
  acceptAttachments,
  maxAttachmentBytes = DEFAULT_MAX_ATTACHMENT_BYTES,
  maxAttachmentCount = DEFAULT_MAX_ATTACHMENTS,
  hideSubmit = false,
  readOnly = false,
}: PillInputBarProps) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [overflowing, setOverflowing] = useState(false);
  const [multiline, setMultiline] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    const needed = el.scrollHeight;
    el.style.height = `${Math.min(needed, maxHeight)}px`;
    setOverflowing(needed > maxHeight);
    setMultiline(needed > MULTILINE_THRESHOLD);
  }, [value, maxHeight]);

  const enabled = canSubmit && !submitting;
  const canCancel = submitting && typeof onCancel === 'function';
  const list = attachments ?? [];
  const attachmentsEnabled = allowAttachments && typeof onAttachmentsChange === 'function';

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    setAttachError(null);
    const files = Array.from(event.target.files ?? []);
    // 重置 input value，允许下次再选同一个文件
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
    const next = files.map(toAttachment);
    onAttachmentsChange([...list, ...next]);
  };

  const removeAttachment = (id: string) => {
    if (!onAttachmentsChange) return;
    onAttachmentsChange(list.filter((a) => a.id !== id));
  };

  return (
    <div>
      {topSlot ? <div className="mb-2">{topSlot}</div> : null}
      {attachmentsEnabled && list.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {list.map((att) => (
            <span
              key={att.id}
              className="inline-flex items-center gap-1.5 rounded-full border border-black/10 bg-white px-2.5 py-1 text-xs text-black/75 shadow-pill"
              title={`${att.name} · ${formatSize(att.size)}`}
            >
              <PaperclipIcon className="h-3 w-3 shrink-0 text-black/55" />
              <span className="max-w-[160px] truncate">{att.name}</span>
              <button
                type="button"
                onClick={() => removeAttachment(att.id)}
                disabled={submitting}
                className="ml-0.5 grid h-4 w-4 place-items-center rounded-full text-black/45 hover:bg-black/[0.06] hover:text-black/85 disabled:cursor-not-allowed disabled:text-black/25 disabled:hover:bg-transparent"
                aria-label={`移除 ${att.name}`}
              >
                <CloseIcon className="h-2.5 w-2.5" />
              </button>
            </span>
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

function toAttachment(file: File): PillAttachment {
  return {
    id: `att_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    name: file.name,
    size: file.size,
    file,
    mimeType: file.type || undefined,
  };
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
