"use client";

import { cn } from "@/lib/cn";

export type SegmentOption<T extends string> = {
  id: T;
  label: string;
};

type SegmentedControlProps<T extends string> = {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
  "aria-label"?: string;
};

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  className,
  "aria-label": ariaLabel,
}: SegmentedControlProps<T>) {
  return (
    <div
      className={cn(
        "flex w-fit gap-1 rounded-lg border border-outline-variant bg-surface-container-low p-1",
        className
      )}
      role="tablist"
      aria-label={ariaLabel}
    >
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          role="tab"
          aria-selected={value === opt.id}
          onClick={() => onChange(opt.id)}
          className={cn(
            "rounded-md px-6 py-2 font-label text-label-md transition-all",
            value === opt.id
              ? "bg-surface-container-highest text-primary editorial-shadow"
              : "text-secondary hover:bg-white/[0.04]"
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
