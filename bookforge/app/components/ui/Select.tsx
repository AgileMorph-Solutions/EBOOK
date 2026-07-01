import { cn } from "@/lib/cn";

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        "w-full appearance-none rounded-lg border border-outline-variant bg-white px-4 py-3 font-body text-body-md text-on-surface focus:border-primary focus:outline-none focus:ring-0",
        className
      )}
      {...props}
    >
      {children}
    </select>
  );
}
