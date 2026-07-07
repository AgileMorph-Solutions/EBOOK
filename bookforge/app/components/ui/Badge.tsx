import { cn } from "@/lib/cn";

const variants = {
  default: "bg-surface-container-high text-on-surface-variant border-white/[0.06]",
  queued: "bg-amber-400/15 text-amber-300 border-amber-400/30",
  waiting: "bg-amber-400/15 text-amber-300 border-amber-400/30",
  running: "bg-primary/15 text-primary border-primary/40",
  done: "bg-tertiary/15 text-tertiary border-tertiary/30",
  failed: "bg-error/15 text-error border-error/30",
  guide: "bg-primary/10 text-primary border-primary/25",
  narrative: "bg-surface-container-high text-secondary border-white/[0.06]",
  verified: "bg-tertiary/15 text-tertiary border-tertiary/30",
} as const;

type BadgeVariant = keyof typeof variants;

export function Badge({
  children,
  variant = "default",
  className,
}: {
  children: React.ReactNode;
  variant?: BadgeVariant;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-2 py-0.5 font-label text-[10px] uppercase tracking-tighter",
        variants[variant] ?? variants.default,
        className
      )}
    >
      {children}
    </span>
  );
}

const statusLabels: Record<string, string> = {
  queued: "Queued",
  waiting: "Preparing",
  running: "Running",
  done: "Done",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: string }) {
  const variant = (["queued", "waiting", "running", "done", "failed"].includes(status)
    ? status
    : "default") as BadgeVariant;
  return (
    <Badge variant={variant}>
      {statusLabels[status] ?? status}
    </Badge>
  );
}
