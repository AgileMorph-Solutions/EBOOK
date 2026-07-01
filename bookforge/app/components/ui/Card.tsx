import { cn } from "@/lib/cn";

const variants = {
  default: "rounded-2xl border border-outline/10 bg-white/60 editorial-shadow",
  glass: "glass-panel rounded-2xl editorial-shadow",
  elevated: "rounded-xl border border-outline/10 bg-white editorial-shadow",
  low: "rounded-xl bg-surface-container-low border border-outline/5",
} as const;

type CardVariant = keyof typeof variants;

export function Card({
  children,
  className,
  variant = "default",
}: {
  children: React.ReactNode;
  className?: string;
  variant?: CardVariant;
}) {
  return (
    <div className={cn(variants[variant], className)}>{children}</div>
  );
}

export function GlassPanel({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Card variant="glass" className={className}>
      {children}
    </Card>
  );
}

export function CardHeader({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("px-6 pt-6", className)}>{children}</div>;
}

export function CardContent({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("px-6 pb-6", className)}>{children}</div>;
}
