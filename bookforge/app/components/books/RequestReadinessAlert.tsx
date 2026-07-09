import { Alert } from "@/components/ui/Alert";
import type { ReadinessResult } from "@/lib/api";

type PromisedCountDetail = {
  promised: number;
  target: number;
  required: number;
  max: number;
};

function parsePromisedCountDetail(detail: string): PromisedCountDetail | null {
  const parts = Object.fromEntries(
    detail.split(/\s+/).map((pair) => {
      const [key, value] = pair.split("=");
      return [key, Number(value)];
    })
  );
  if (
    !Number.isFinite(parts.promised) ||
    !Number.isFinite(parts.target) ||
    !Number.isFinite(parts.required) ||
    !Number.isFinite(parts.max)
  ) {
    return null;
  }
  return {
    promised: parts.promised,
    target: parts.target,
    required: parts.required,
    max: parts.max,
  };
}

function StatRow({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: "bad" | "ok";
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-outline/10 py-2 last:border-0">
      <span className="text-secondary">{label}</span>
      <span
        className={
          highlight === "bad"
            ? "font-semibold text-error"
            : highlight === "ok"
              ? "font-semibold text-tertiary"
              : "font-medium"
        }
      >
        {value}
      </span>
    </div>
  );
}

export function RequestReadinessAlert({
  readiness,
}: {
  readiness: ReadinessResult;
}) {
  const detail = readiness.checks.request?.detail ?? "";
  const stats = parsePromisedCountDetail(detail);
  const exceedsMax = stats !== null && stats.required > stats.max;

  return (
    <Alert variant="warning" className="px-5 py-4">
      <p className="font-label text-label-md font-semibold">{readiness.title}</p>
      <p className="mt-2 font-body text-body-sm leading-relaxed">
        {readiness.message}
      </p>

      {stats && (
        <div className="mt-4 rounded-md border border-outline/15 bg-surface/40 px-4 py-1">
          <StatRow
            label="Items promised in topic"
            value={String(stats.promised)}
          />
          <StatRow
            label="Your target pages"
            value={String(stats.target)}
            highlight={stats.target >= stats.required ? "ok" : "bad"}
          />
          <StatRow
            label="Pages needed for that count"
            value={`${stats.required} (~2 per item)`}
            highlight={stats.target < stats.required ? "bad" : undefined}
          />
          <StatRow label="Maximum allowed" value={String(stats.max)} />
        </div>
      )}

      {exceedsMax && (
        <p className="mt-3 font-body text-body-sm font-medium text-on-primary-fixed-variant">
          Even at {stats.max} pages you cannot fit {stats.promised} items. Change
          the topic (e.g. drop &quot;{stats.promised}&quot;) or use Section limit
          for a smaller book.
        </p>
      )}
    </Alert>
  );
}
