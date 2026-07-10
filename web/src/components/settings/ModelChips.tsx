import { useState, type KeyboardEvent } from 'react';

interface ModelChipsProps {
  values: string[];
  onChange(next: string[]): void;
  disabled?: boolean;
  emptyPlaceholder?: string;
  morePlaceholder?: string;
}

export function ModelChips({
  values,
  onChange,
  disabled = false,
  emptyPlaceholder = '输入模型 ID 后回车',
  morePlaceholder = '继续添加…',
}: ModelChipsProps) {
  const [draft, setDraft] = useState('');
  const modelValues = uniqueValues(values);

  function commitDraft() {
    const next = draft.trim();
    if (!next) {
      if (draft.length > 0) setDraft('');
      return;
    }
    if (modelValues.includes(next)) {
      setDraft('');
      return;
    }
    onChange([...modelValues, next]);
    setDraft('');
  }

  function remove(index: number) {
    onChange(modelValues.filter((_, i) => i !== index));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault();
      commitDraft();
      return;
    }
    if (event.key === 'Backspace' && draft.length === 0 && modelValues.length > 0) {
      event.preventDefault();
      onChange(modelValues.slice(0, -1));
    }
  }

  return (
    <div
      className={`flex flex-wrap items-center gap-1.5 rounded-xl border border-black/10 bg-white px-2 py-1.5 transition focus-within:border-black/30 ${disabled ? 'cursor-wait opacity-60' : ''}`}
    >
      {modelValues.map((tag, index) => (
        <span
          key={`${tag}-${index}`}
          className="inline-flex items-center gap-1 rounded-full bg-black px-2.5 py-1 font-mono text-[11px] text-white"
        >
          <span className="max-w-[180px] truncate">{tag}</span>
          <button
            type="button"
            onClick={() => remove(index)}
            disabled={disabled}
            aria-label={`移除 ${tag}`}
            className="-mr-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-white/70 transition hover:bg-white/15 hover:text-white disabled:cursor-not-allowed"
          >
            ×
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={commitDraft}
        disabled={disabled}
        spellCheck={false}
        placeholder={modelValues.length === 0 ? emptyPlaceholder : morePlaceholder}
        className="min-w-[140px] flex-1 bg-transparent px-1 py-1 font-mono text-xs text-black outline-none placeholder:text-black/30 disabled:cursor-wait"
      />
    </div>
  );
}

function uniqueValues(values: string[]) {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}
