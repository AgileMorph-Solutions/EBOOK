import { cn } from "@/lib/cn";

export function Spinner({
  className,
  label = "Loading",
}: {
  className?: string;
  label?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)} role="status">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-outline-variant border-t-primary"
        aria-hidden
      />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-surface-container-highest", className)}
      aria-hidden
    />
  );
}
