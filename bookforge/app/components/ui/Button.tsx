import { cn } from "@/lib/cn";

const variants = {
  primary:
    "bg-primary text-on-primary editorial-shadow hover:bg-primary-container active:scale-[0.98]",
  accent:
    "bg-primary text-on-primary editorial-shadow hover:bg-primary-container active:scale-[0.98]",
  outline:
    "border border-outline bg-transparent text-on-surface hover:bg-surface-container-low active:scale-[0.98]",
  ghost:
    "bg-transparent text-on-surface hover:bg-surface-container-low active:scale-[0.98]",
  destructive:
    "text-error hover:bg-error-container/20 active:scale-[0.98]",
  surface:
    "bg-surface-container-highest border border-outline/10 text-on-surface hover:bg-white active:scale-[0.98]",
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
