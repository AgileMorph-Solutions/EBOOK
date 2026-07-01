import { cn } from "@/lib/cn";

const variants = {
  error: "border-error/30 bg-error-container text-on-error-container",
  info: "border-outline/20 bg-surface-container-low text-on-surface",
  success: "border-tertiary/30 bg-tertiary/5 text-tertiary",
  warning: "border-primary/30 bg-primary-fixed/30 text-on-primary-fixed-variant",
} as const;

type AlertVariant = keyof typeof variants;

export function Alert({
  children,
  variant = "error",
  className,
}: {
  children: React.ReactNode;
  variant?: AlertVariant;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border px-4 py-3 font-body text-body-sm",
        variants[variant],
        className
      )}
      role="alert"
    >
      {children}
    </div>
  );
}
