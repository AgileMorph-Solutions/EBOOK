import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

export type BreadcrumbItem = {
  label: string;
  href?: string;
};

export function Breadcrumb({
  items,
  className,
}: {
  items: BreadcrumbItem[];
  className?: string;
}) {
  return (
    <nav
      className={cn(
        "flex items-center gap-2 font-label text-label-sm text-secondary",
        className
      )}
      aria-label="Breadcrumb"
    >
      {items.map((item, i) => (
        <span key={i} className="flex items-center gap-2">
          {i > 0 && (
            <Icon name="chevron_right" className="text-secondary" size={12} />
          )}
          {item.href ? (
            <Link
              href={item.href}
              className="hover:text-primary transition-colors"
            >
              {item.label}
            </Link>
          ) : (
            <span className="text-on-surface">{item.label}</span>
          )}
        </span>
      ))}
    </nav>
  );
}
