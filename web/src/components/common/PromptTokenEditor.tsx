import {
  forwardRef,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  type ClipboardEvent,
  type CompositionEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import type { PromptTokenDefinition } from './promptTokens';

interface PromptTokenEditorProps {
  value: string;
  tokens: PromptTokenDefinition[];
  onChange(value: string): void;
  onBlur?(): void;
  placeholder?: string;
  readOnly?: boolean;
  className?: string;
  ariaLabel?: string;
}

export interface PromptTokenEditorHandle {
  insertToken(value: string): void;
}

const TOKEN_ATTRIBUTE = 'data-prompt-token';

export const PromptTokenEditor = forwardRef<PromptTokenEditorHandle, PromptTokenEditorProps>(function PromptTokenEditor({
  value,
  tokens,
  onChange,
  onBlur,
  placeholder = '',
  readOnly = false,
  className = '',
  ariaLabel = '提示词',
}, ref) {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const composingRef = useRef(false);
  const caretOffsetRef = useRef(value.length);
  const valueRef = useRef(value);
  valueRef.current = value;
  const tokenSignature = useMemo(
    () => tokens.map((token) => `${token.kind}:${token.value}:${token.label}:${token.description ?? ''}`).join('\u0000'),
    [tokens],
  );

  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const current = readPromptValue(editor);
    if (current === value && editor.dataset.tokenSignature === tokenSignature) return;
    const selectionOffset = document.activeElement === editor ? getCaretOffset(editor) : null;
    renderPromptValue(editor, value, tokens);
    editor.dataset.tokenSignature = tokenSignature;
    if (selectionOffset !== null) {
      const nextOffset = Math.min(selectionOffset, value.length);
      caretOffsetRef.current = nextOffset;
      setCaretOffset(editor, nextOffset);
    }
  }, [tokenSignature, tokens, value]);

  const syncValue = () => {
    const editor = editorRef.current;
    if (!editor) return;
    const next = readPromptValue(editor);
    const caretOffset = getCaretOffset(editor);
    if (caretOffset !== null) caretOffsetRef.current = caretOffset;
    if (next !== valueRef.current) {
      valueRef.current = next;
      onChange(next);
    }
  };

  const rememberCaret = () => {
    const editor = editorRef.current;
    if (!editor) return;
    const caretOffset = getCaretOffset(editor);
    if (caretOffset !== null) caretOffsetRef.current = caretOffset;
  };

  useImperativeHandle(ref, () => ({
    insertToken(tokenValue: string) {
      const editor = editorRef.current;
      if (!editor || readOnly) return;
      const current = valueRef.current;
      const offset = Math.min(caretOffsetRef.current, current.length);
      const before = current.slice(0, offset);
      const after = current.slice(offset);
      const prefix = needsTokenSpacing(before[before.length - 1] ?? '') ? ' ' : '';
      const suffix = needsTokenSpacing(after[0] ?? '') ? ' ' : '';
      const inserted = `${prefix}${tokenValue}${suffix}`;
      const next = `${before}${inserted}${after}`;
      const nextOffset = offset + inserted.length;
      valueRef.current = next;
      caretOffsetRef.current = nextOffset;
      onChange(next);
      renderPromptValue(editor, next, tokens);
      editor.dataset.tokenSignature = tokenSignature;
      editor.focus();
      setCaretOffset(editor, nextOffset);
    },
  }), [onChange, readOnly, tokenSignature, tokens]);

  const handleInput = (_event: FormEvent<HTMLDivElement>) => {
    if (!composingRef.current) syncValue();
  };

  const handleCompositionEnd = (_event: CompositionEvent<HTMLDivElement>) => {
    composingRef.current = false;
    syncValue();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (readOnly) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      insertPlainText('\n');
      syncValue();
      return;
    }
    if (event.key !== 'Backspace' && event.key !== 'Delete') return;
    const editor = editorRef.current;
    const selection = window.getSelection();
    if (!editor || !selection || !selection.isCollapsed || selection.rangeCount === 0) return;
    const token = adjacentToken(editor, selection.getRangeAt(0), event.key === 'Backspace' ? 'before' : 'after');
    if (!token) return;
    event.preventDefault();
    const caretOffset = getCaretOffset(editor) ?? 0;
    const tokenValue = token.getAttribute(TOKEN_ATTRIBUTE) ?? '';
    token.remove();
    syncValue();
    const nextOffset = event.key === 'Backspace' ? Math.max(0, caretOffset - tokenValue.length) : caretOffset;
    caretOffsetRef.current = nextOffset;
    setCaretOffset(editor, nextOffset);
  };

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    if (readOnly) return;
    event.preventDefault();
    insertPlainText(event.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n'));
    syncValue();
  };

  const handleCopy = (event: ClipboardEvent<HTMLDivElement>) => {
    const editor = editorRef.current;
    const offsets = editor ? getSelectionOffsets(editor) : null;
    if (!editor || !offsets || offsets.start === offsets.end) return;
    event.preventDefault();
    event.clipboardData.setData('text/plain', readPromptValue(editor).slice(offsets.start, offsets.end));
  };

  const handleCut = (event: ClipboardEvent<HTMLDivElement>) => {
    if (readOnly) return;
    const editor = editorRef.current;
    const selection = window.getSelection();
    const offsets = editor ? getSelectionOffsets(editor) : null;
    if (!editor || !selection || !offsets || offsets.start === offsets.end || selection.rangeCount === 0) return;
    event.preventDefault();
    event.clipboardData.setData('text/plain', readPromptValue(editor).slice(offsets.start, offsets.end));
    selection.getRangeAt(0).deleteContents();
    syncValue();
  };

  return (
    <div
      ref={editorRef}
      role="textbox"
      aria-label={ariaLabel}
      aria-multiline="true"
      aria-readonly={readOnly}
      contentEditable={!readOnly}
      suppressContentEditableWarning
      data-placeholder={placeholder}
      className={`${className} whitespace-pre-wrap break-words empty:before:pointer-events-none empty:before:text-black/35 empty:before:content-[attr(data-placeholder)]`}
      spellCheck
      onInput={handleInput}
      onCompositionStart={() => {
        composingRef.current = true;
      }}
      onCompositionEnd={handleCompositionEnd}
      onKeyDown={handleKeyDown}
      onKeyUp={rememberCaret}
      onMouseUp={rememberCaret}
      onPaste={handlePaste}
      onCopy={handleCopy}
      onCut={handleCut}
      onBlur={() => {
        rememberCaret();
        onBlur?.();
      }}
    />
  );
});

function renderPromptValue(editor: HTMLElement, value: string, tokens: PromptTokenDefinition[]): void {
  const fragment = document.createDocumentFragment();
  for (const part of splitPrompt(value, tokens)) {
    if (typeof part === 'string') {
      fragment.append(document.createTextNode(part));
      continue;
    }
    const tag = document.createElement('span');
    tag.setAttribute(TOKEN_ATTRIBUTE, part.value);
    tag.setAttribute('contenteditable', 'false');
    tag.className = promptTokenClassName();
    tag.textContent = part.label;
    const kindLabel = promptTokenKindLabel(part.kind);
    tag.title = `${kindLabel}：${part.value}${part.description ? `\n${part.description}` : ''}`;
    fragment.append(tag);
  }
  editor.replaceChildren(fragment);
}

function promptTokenClassName(): string {
  return 'mx-0.5 inline-flex select-all items-center rounded-md border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-xs font-medium leading-none text-blue-700';
}

function promptTokenKindLabel(kind: PromptTokenDefinition['kind']): string {
  if (kind === 'skill') return 'Skill';
  return 'MCP';
}

function splitPrompt(value: string, tokens: PromptTokenDefinition[]): Array<string | PromptTokenDefinition> {
  if (!value || tokens.length === 0) return [value];
  const parts: Array<string | PromptTokenDefinition> = [];
  let textStart = 0;
  let index = 0;
  while (index < value.length) {
    const token = tokens.find((candidate) => tokenMatchesAt(value, index, candidate.value));
    if (!token) {
      index += 1;
      continue;
    }
    if (textStart < index) parts.push(value.slice(textStart, index));
    parts.push(token);
    index += token.value.length;
    textStart = index;
  }
  if (textStart < value.length) parts.push(value.slice(textStart));
  return parts.length ? parts : [''];
}

function tokenMatchesAt(text: string, index: number, token: string): boolean {
  if (!text.startsWith(token, index)) return false;
  const before = index > 0 ? text[index - 1] : '';
  const after = text[index + token.length] ?? '';
  if (isPathSeparator(before) || isPathSeparator(after) || isIdentifierCharacter(before) || isIdentifierCharacter(after)) return false;
  return true;
}

function isIdentifierCharacter(value: string): boolean {
  return /[\p{L}\p{N}_-]/u.test(value);
}

function needsTokenSpacing(value: string): boolean {
  return /[\p{L}\p{N}_-]/u.test(value);
}

function isPathSeparator(value: string): boolean {
  return value === '.' || value === '/' || value === '\\';
}

function readPromptValue(editor: HTMLElement): string {
  return [...editor.childNodes].map(readNodeValue).join('');
}

function readNodeValue(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? '';
  if (!(node instanceof HTMLElement)) return '';
  const token = node.getAttribute(TOKEN_ATTRIBUTE);
  if (token !== null) return token;
  if (node.tagName === 'BR') return '\n';
  const content = [...node.childNodes].map(readNodeValue).join('');
  return node === node.parentElement?.lastChild ? content : `${content}\n`;
}

function insertPlainText(text: string): void {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return;
  const range = selection.getRangeAt(0);
  range.deleteContents();
  const textNode = document.createTextNode(text);
  range.insertNode(textNode);
  range.setStartAfter(textNode);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
}

function getSelectionOffsets(editor: HTMLElement): { start: number; end: number } | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return null;
  return {
    start: pointOffset(editor, range.startContainer, range.startOffset),
    end: pointOffset(editor, range.endContainer, range.endOffset),
  };
}

function getCaretOffset(editor: HTMLElement): number | null {
  return getSelectionOffsets(editor)?.end ?? null;
}

function pointOffset(editor: HTMLElement, target: Node, targetOffset: number): number {
  let total = 0;
  let result = 0;
  let found = false;

  const visit = (node: Node) => {
    if (found) return;
    if (node === target) {
      if (node.nodeType === Node.TEXT_NODE) {
        result = total + Math.min(targetOffset, node.textContent?.length ?? 0);
      } else {
        result = total + [...node.childNodes]
          .slice(0, targetOffset)
          .reduce((sum, child) => sum + logicalNodeLength(child), 0);
      }
      found = true;
      return;
    }
    if (node instanceof HTMLElement && node.hasAttribute(TOKEN_ATTRIBUTE)) {
      total += logicalNodeLength(node);
      return;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      total += node.textContent?.length ?? 0;
      return;
    }
    for (const child of node.childNodes) visit(child);
  };

  visit(editor);
  return found ? result : total;
}

function logicalNodeLength(node: Node): number {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent?.length ?? 0;
  if (!(node instanceof HTMLElement)) return 0;
  const token = node.getAttribute(TOKEN_ATTRIBUTE);
  if (token !== null) return token.length;
  if (node.tagName === 'BR') return 1;
  return [...node.childNodes].reduce((sum, child) => sum + logicalNodeLength(child), 0);
}

function setCaretOffset(editor: HTMLElement, offset: number): void {
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  let remaining = Math.max(0, offset);
  for (let index = 0; index < editor.childNodes.length; index += 1) {
    const child = editor.childNodes[index];
    const length = logicalNodeLength(child);
    if (child.nodeType === Node.TEXT_NODE && remaining <= length) {
      range.setStart(child, remaining);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
      return;
    }
    if (child instanceof HTMLElement && child.hasAttribute(TOKEN_ATTRIBUTE) && remaining <= length) {
      range.setStart(editor, remaining === 0 ? index : index + 1);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
      return;
    }
    remaining -= length;
  }
  range.selectNodeContents(editor);
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);
}

function adjacentToken(editor: HTMLElement, range: Range, direction: 'before' | 'after'): HTMLElement | null {
  const container = range.startContainer;
  const offset = range.startOffset;
  let candidate: Node | null = null;
  if (container === editor) {
    candidate = editor.childNodes[direction === 'before' ? offset - 1 : offset] ?? null;
  } else if (container.nodeType === Node.TEXT_NODE) {
    const textLength = container.textContent?.length ?? 0;
    if (direction === 'before' && offset === 0) candidate = container.previousSibling;
    if (direction === 'after' && offset === textLength) candidate = container.nextSibling;
  }
  return candidate instanceof HTMLElement && candidate.hasAttribute(TOKEN_ATTRIBUTE) ? candidate : null;
}
