import { cn } from "@/lib/cn";

type ProgressBarProps = {
  value: number;
  max?: number;
  color?: "tertiary" | "primary";
  className?: string;
};

export function ProgressBar({
  value,
  max = 100,
  color = "tertiary",
  className,
}: ProgressBarProps) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <div
      className={cn(
        "h-1.5 w-full overflow-hidden rounded-full bg-surface-container-highest",
        className
      )}
      role="progressbar"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
    >
      <div
        className={cn(
          "h-full rounded-full transition-all duration-500",
          color === "tertiary" ? "bg-tertiary" : "bg-primary"
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
