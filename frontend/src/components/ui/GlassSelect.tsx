"use client";

import { useState, useRef, useEffect } from "react";

export type GlassSelectOption = { value: string; label: string };

type GlassSelectProps = {
  value: string;
  onChange: (value: string) => void;
  options: GlassSelectOption[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  id?: string;
};

export default function GlassSelect({
  value,
  onChange,
  options,
  placeholder = "Select…",
  disabled,
  className = "",
  id,
}: GlassSelectProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const selected = options.find((o) => o.value === value);
  const display =
    selected?.label ?? (value === "" ? placeholder : value || placeholder);

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        id={id}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => !disabled && setOpen((o) => !o)}
        className={
          "flex w-full items-center justify-between gap-2 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-left text-sm outline-none transition-colors " +
          "focus:border-purple-500 focus:ring-1 focus:ring-purple-500/40 " +
          "disabled:cursor-not-allowed disabled:opacity-50 " +
          (!selected && value === "" ? "text-slate-500" : "text-slate-200")
        }
      >
        <span className="truncate">{display}</span>
        <span className="shrink-0 text-[10px] text-slate-500" aria-hidden>
          {open ? "▲" : "▼"}
        </span>
      </button>
      {open && !disabled && (
        <ul
          role="listbox"
          className="absolute top-full z-[200] mt-1 max-h-48 min-w-full w-full overflow-y-auto rounded-lg border border-white/12 bg-slate-950/98 py-1 shadow-2xl shadow-black/60 backdrop-blur-md"
        >
          {options.map((opt) => (
            <li
              key={opt.value === "" ? "__empty__" : opt.value}
              role="presentation"
            >
              <button
                type="button"
                role="option"
                aria-selected={value === opt.value}
                className={
                  "w-full px-3 py-2 text-left text-sm transition-colors " +
                  (value === opt.value
                    ? "bg-purple-600/40 text-white"
                    : "text-slate-200 hover:bg-white/10")
                }
                onClick={() => {
                  onChange(opt.value);
                  setOpen(false);
                }}
              >
                {opt.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
