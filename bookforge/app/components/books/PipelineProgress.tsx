import { GUIDE_PIPELINE_STAGES } from "@/lib/constants";
import { cn } from "@/lib/cn";
import { Icon } from "@/components/ui/Icon";
import { GlassPanel } from "@/components/ui/Card";

type PipelineProgressProps = {
  status: string;
  stage?: string;
  bookKind?: string;
  elapsed?: string;
  progress?: number;
  etaSeconds?: number;
};

function formatSecs(s?: number): string {
  if (s == null || !Number.isFinite(s) || s < 0) return "";
  const total = Math.round(s);
  const m = Math.floor(total / 60);
  const sec = total % 60;
  if (m <= 0) return `${sec}s`;
  if (m < 60) return `${m}m ${sec}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export function PipelineProgress({
  status,
  stage,
  bookKind,
  elapsed,
  progress,
  etaSeconds,
}: PipelineProgressProps) {
  const stages = GUIDE_PIPELINE_STAGES;

  // The fix-loop reports dynamic stages ("revise (1)", "revise (2)"…); they all
  // belong to the QA phase, so map them onto the "qa" chip.
  const normStage = stage?.startsWith("revise") ? "qa" : stage;
  const stageIndex = normStage
    ? stages.findIndex((s) => s.key === normStage)
    : -1;

  const isActive =
    status === "queued" || status === "running" || status === "waiting";

  if (!isActive) return null;

  const statusLabel =
    status === "queued" ? "queued" : status === "waiting" ? "preparing" : "running";

  const pct = Math.max(0, Math.min(100, Math.round((progress ?? 0) * 100)));
  const eta = formatSecs(etaSeconds);

  return (
    <GlassPanel className="rounded-xl p-6 editorial-shadow">
      <div className="flex items-center gap-3">
        <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
        <p className="font-label text-label-md font-medium text-on-surface">
          Pipeline {statusLabel}…
        </p>
        {elapsed && (
          <span className="ml-auto font-label text-label-sm text-secondary">
            {elapsed}
          </span>
        )}
      </div>

      <div className="mt-5">
        <div className="flex items-center justify-between font-label text-label-sm">
          <span className="font-medium text-on-surface tabular-nums">{pct}%</span>
          {eta && (
            <span className="text-secondary tabular-nums">
              {status === "running" ? `~${eta} remaining` : `~${eta} estimated`}
            </span>
          )}
        </div>
        <div
          className="mt-2 h-2 w-full overflow-hidden rounded-full bg-surface-container-low"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-700 ease-out"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <ol className="mt-6 flex flex-wrap gap-2">
        {stages.map((s, i) => {
          const done = stageIndex > i;
          const current =
            stageIndex === i ||
            (stageIndex < 0 && i === 0 && status === "running");
          return (
            <li
              key={s.key}
              className={cn(
                "flex items-center gap-1.5 rounded-full border px-3 py-1 font-label text-label-sm",
                done && "border-tertiary/30 bg-tertiary/10 text-tertiary",
                current &&
                  !done &&
                  "border-primary/40 bg-primary/15 text-primary ember-glow",
                !done &&
                  !current &&
                  "border-white/[0.06] bg-surface-container-low text-secondary"
              )}
            >
              {done ? (
                <Icon name="check" size={12} />
              ) : current ? (
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
              ) : null}
              {s.label}
            </li>
          );
        })}
      </ol>
    </GlassPanel>
  );
}
