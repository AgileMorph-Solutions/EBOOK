import { cn } from "@/lib/cn";
import { Icon } from "@/components/ui/Icon";
import { StatusBadge } from "@/components/ui/Badge";

type PageHeaderProps = {
  title: string;
  status?: string;
  subtitle?: string;
  className?: string;
};

export function PageHeader({
  title,
  status,
  subtitle,
  className,
}: PageHeaderProps) {
  return (
    <div className={className}>
      <h1 className="font-display text-display-lg-mobile md:text-display-lg text-on-surface leading-tight">
        {title}
      </h1>
      {(status || subtitle) && (
        <div className="mt-2 flex flex-wrap items-center gap-3">
          {status === "done" && (
            <span className="inline-flex items-center rounded-full bg-tertiary-fixed px-3 py-1 font-label text-label-sm text-on-tertiary-fixed-variant">
              <Icon name="check_circle" filled className="mr-1" size={14} />
              Done
            </span>
          )}
          {status && status !== "done" && <StatusBadge status={status} />}
          {subtitle && (
            <span className="font-body text-body-sm text-secondary">{subtitle}</span>
          )}
        </div>
      )}
    </div>
  );
}
