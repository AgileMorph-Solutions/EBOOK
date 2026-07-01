import { cn } from "@/lib/cn";

type IconProps = {
  name: string;
  filled?: boolean;
  className?: string;
  size?: number;
};

export function Icon({ name, filled = false, className, size = 24 }: IconProps) {
  return (
    <span
      className={cn(
        "material-symbols-outlined inline-flex items-center justify-center leading-none",
        filled && "filled",
        className
      )}
      style={{ fontSize: size }}
      aria-hidden
    >
      {name}
    </span>
  );
}
