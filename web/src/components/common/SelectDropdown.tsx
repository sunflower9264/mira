import { useEffect, useRef, useState } from 'react';

type SelectOption = string | { label: string; value: string };

interface SelectDropdownProps {
  value: string;
  options: SelectOption[];
  onChange: (next: string) => void;
  placeholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  className?: string;
  buttonClassName?: string;
  menuClassName?: string;
}

export function SelectDropdown({
  value,
  options,
  onChange,
  placeholder = '请选择',
  emptyLabel = '没有可用模型。',
  disabled = false,
  className = '',
  buttonClassName,
  menuClassName,
}: SelectDropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const normalized = options.map((option) =>
    typeof option === 'string' ? { label: option, value: option } : option,
  );
  const selected = normalized.find((option) => option.value === value);

  useEffect(() => {
    if (disabled) {
      setOpen(false);
      return;
    }
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [disabled, open]);

  return (
    <div ref={ref} className={`relative ${className}`}>
      <button
        type="button"
        className={`${buttonClassName ?? selectButtonCls} justify-between disabled:cursor-not-allowed disabled:opacity-60`}
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
      >
        <span className="truncate">{selected?.label ?? placeholder}</span>
        <ChevronDownIcon className="h-4 w-4 shrink-0 text-black/35" />
      </button>
      {open && (
        <div className={menuClassName ?? selectMenuCls}>
          {normalized.length === 0 ? (
            <div className="px-3 py-2 text-sm text-black/45">{emptyLabel}</div>
          ) : (
            normalized.map((option) => (
              <button
                key={option.value}
                type="button"
                className={`w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-black/[0.03] ${
                  option.value === value ? 'bg-black/[0.04] text-black' : 'text-black/70'
                }`}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
              >
                {option.label}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

function ChevronDownIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

const selectButtonCls = 'flex h-9 w-44 max-w-full items-center rounded-full bg-[#F9F9F9] px-3 text-sm outline-none hover:bg-black/[0.04]';
const selectMenuCls = 'absolute left-0 top-full z-30 mt-1 max-h-56 w-56 overflow-y-auto rounded-xl border border-black/10 bg-white p-1 shadow-lg';
