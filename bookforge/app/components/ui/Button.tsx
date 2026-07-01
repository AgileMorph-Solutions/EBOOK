import { cn } from "@/lib/cn";

const variants = {
  primary:
    "bg-primary text-on-primary ember-glow hover:bg-primary-fixed-dim hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98]",
  accent:
    "bg-primary text-on-primary ember-glow hover:bg-primary-fixed-dim hover:-translate-y-0.5 active:scale-[0.98]",
  outline:
    "border border-outline/60 bg-transparent text-on-surface hover:border-primary hover:text-primary hover:bg-primary/5 active:scale-[0.98]",
  ghost:
    "bg-transparent text-on-surface hover:bg-white/[0.05] active:scale-[0.98]",
  destructive:
    "text-error hover:bg-error/10 active:scale-[0.98]",
  surface:
    "bg-surface-container-high border border-white/[0.06] text-on-surface hover:bg-surface-container-highest active:scale-[0.98]",
} as const;

type ButtonVariant = keyof typeof variants;

export function Button({
  children,
  variant = "primary",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg px-5 py-2.5 font-label text-label-md transition-all disabled:cursor-not-allowed disabled:opacity-40",
        variants[variant],
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}
