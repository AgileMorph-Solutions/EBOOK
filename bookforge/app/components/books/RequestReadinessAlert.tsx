import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import type { FeasibilityOption, ReadinessResult } from "@/lib/api";

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
  onChooseOption,
}: {
  readiness: ReadinessResult;
  onChooseOption?: (option: FeasibilityOption) => void;
}) {
  const feas = readiness.feasibility;
  const exceedsMax =
    feas != null &&
    feas.promised != null &&
    feas.min_pages_required > feas.target_pages;

  if (feas?.status === "compact") {
    return (
      <Alert variant="info" className="px-5 py-4">
        <p className="font-label text-label-md font-semibold">{readiness.title}</p>
        <p className="mt-2 font-body text-body-sm leading-relaxed">
          {feas.message || readiness.message}
        </p>
        {feas.promised != null && (
          <div className="mt-4 rounded-md border border-outline/15 bg-surface/40 px-4 py-1">
            <StatRow label="Items kept" value={String(feas.promised)} highlight="ok" />
            <StatRow label="Target pages" value={String(feas.target_pages)} />
            <StatRow
              label="Layout"
              value="Shorter entries (compact)"
              highlight="ok"
            />
          </div>
        )}
      </Alert>
    );
  }

  return (
    <Alert variant="warning" className="px-5 py-4">
      <p className="font-label text-label-md font-semibold">{readiness.title}</p>
      <p className="mt-2 font-body text-body-sm leading-relaxed">
        {feas?.message || readiness.message}
      </p>

      {feas && feas.promised != null && (
        <div className="mt-4 rounded-md border border-outline/15 bg-surface/40 px-4 py-1">
          <StatRow
            label="Items promised in topic"
            value={String(feas.promised)}
          />
          <StatRow
            label="Your target pages"
            value={String(feas.target_pages)}
            highlight={exceedsMax ? "bad" : "ok"}
          />
          <StatRow
            label="Pages needed to keep that count"
            value={String(feas.min_pages_required)}
            highlight={exceedsMax ? "bad" : undefined}
          />
          <StatRow
            label="Max items at current pages"
            value={String(feas.max_entries_at_target)}
          />
        </div>
      )}

      {feas?.status === "infeasible" && feas.options.length > 0 && (
        <div className="mt-4 space-y-2">
          <p className="font-label text-label-sm font-semibold text-on-primary-fixed-variant">
            Choose how to continue
          </p>
          <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            {feas.options.map((opt) => (
              <Button
                key={opt.id}
                type="button"
                variant="outline"
                className="justify-start text-left"
                onClick={() => onChooseOption?.(opt)}
              >
                {opt.label}
              </Button>
            ))}
          </div>
          <p className="font-body text-body-sm text-secondary">
            We will not reduce the item count unless you choose that option.
          </p>
        </div>
      )}
    </Alert>
  );
}
