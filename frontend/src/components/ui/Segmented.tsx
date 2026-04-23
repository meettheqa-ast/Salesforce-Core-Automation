"use client";

export type SegmentedOption<T extends string> = { value: T; label: string; icon?: string };

interface SegmentedProps<T extends string> {
  value: T;
  onChange: (v: T) => void;
  options: SegmentedOption<T>[];
  size?: "sm" | "md";
  className?: string;
}

export default function Segmented<T extends string>({
  value,
  onChange,
  options,
  size = "sm",
  className = "",
}: SegmentedProps<T>) {
  const padY = size === "sm" ? "py-1" : "py-1.5";
  const padX = size === "sm" ? "px-2.5" : "px-3";
  const text = size === "sm" ? "text-[11px]" : "text-xs";
  return (
    <div className={`inline-flex glass rounded-lg p-0.5 ${className}`}>
      {options.map((opt) => {
        const active = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={
              `flex items-center gap-1 ${padX} ${padY} ${text} font-medium rounded-md transition-all ` +
              (active
                ? "bg-purple-600/30 text-purple-200"
                : "text-slate-400 hover:text-slate-200")
            }
          >
            {opt.icon && <span className="text-[10px]">{opt.icon}</span>}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
