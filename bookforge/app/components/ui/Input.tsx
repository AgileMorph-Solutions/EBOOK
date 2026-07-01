import { cn } from "@/lib/cn";

export function Input({
  className,
  variant = "default",
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & {
  variant?: "default" | "underline";
}) {
  return (
    <input
      className={cn(
        "w-full bg-white text-on-surface font-body text-body-md transition-all focus:outline-none focus:ring-0 disabled:opacity-60",
        variant === "underline"
          ? "border-b-2 border-outline-variant focus:border-primary rounded-t-lg px-4 py-3"
          : "rounded-xl border border-outline-variant px-4 py-3 focus:border-primary",
        className
      )}
      {...props}
    />
  );
}

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        "custom-scrollbar w-full resize-none rounded-xl border border-outline-variant bg-white p-6 font-body text-body-md text-on-surface transition-all focus:border-primary focus:outline-none focus:ring-0",
        className
      )}
      {...props}
    />
  );
}

export function Label({
  children,
  className,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        "flex items-center gap-1 font-label text-label-md text-on-surface",
        className
      )}
      {...props}
    >
      {children}
    </label>
  );
}
