import { cn } from "@/lib/cn";
import { Icon } from "@/components/ui/Icon";

export function DetailsCollapsible({
  summary,
  children,
  className,
}: {
  summary: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <details
      className={cn(
        "group overflow-hidden rounded-xl border border-outline-variant/30",
        className
      )}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between p-4 transition-colors hover:bg-surface-container-low">
        <span className="font-label text-label-md text-secondary">{summary}</span>
        <Icon
          name="expand_more"
          className="text-secondary transition-transform group-open:rotate-180"
        />
      </summary>
      <div className="space-y-6 border-t border-outline-variant/20 bg-surface-container-lowest/50 p-6">
        {children}
      </div>
    </details>
  );
}

export function PillGroup({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-4">
      <span className="font-label text-label-sm uppercase tracking-widest text-primary">
        {label}
      </span>
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(opt)}
            className={cn(
              "rounded-full border px-4 py-2 font-body text-body-sm transition-colors",
              value === opt
                ? "border-primary bg-primary/5 text-primary"
                : "border-outline-variant text-on-surface hover:border-primary"
            )}
          >
            {opt}
          </button>
        ))}
      </div>
    </div>
  );
}
