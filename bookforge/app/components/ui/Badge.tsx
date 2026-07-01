import { cn } from "@/lib/cn";

const variants = {
  default: "bg-surface-container-low text-on-surface border-outline-variant/30",
  queued: "bg-amber-100 text-amber-900 border-amber-200",
  running: "bg-primary-container text-white border-primary-container",
  done: "bg-outline-variant text-on-surface-variant border-outline-variant",
  failed: "bg-error-container text-on-error-container border-error/30",
  guide: "bg-primary/5 text-primary border-primary/20",
  narrative: "bg-surface-container-low text-secondary border-outline-variant/30",
  verified: "bg-tertiary-container text-on-tertiary-container border-tertiary-container",
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
  running: "Running",
  done: "Done",
  failed: "Failed",
};

export function StatusBadge({ status }: { status: string }) {
  const variant = (["queued", "running", "done", "failed"].includes(status)
    ? status
    : "default") as BadgeVariant;
  return (
    <Badge variant={variant}>
      {statusLabels[status] ?? status}
    </Badge>
  );
}
