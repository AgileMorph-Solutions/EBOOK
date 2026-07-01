import { PIPELINE_STAGES } from "@/lib/constants";
import { cn } from "@/lib/cn";
import { Icon } from "@/components/ui/Icon";
import { GlassPanel } from "@/components/ui/Card";

type PipelineProgressProps = {
  status: string;
  stage?: string;
  bookKind?: string;
  elapsed?: string;
};

export function PipelineProgress({
  status,
  stage,
  bookKind,
  elapsed,
}: PipelineProgressProps) {
  const stages =
    bookKind === "narrative"
      ? PIPELINE_STAGES
      : PIPELINE_STAGES.filter((s) => s.key !== "qa");

  const stageIndex = stage ? stages.findIndex((s) => s.key === stage) : -1;

  const isActive = status === "queued" || status === "running";

  if (!isActive) return null;

  return (
    <GlassPanel className="rounded-xl p-6 editorial-shadow">
      <div className="flex items-center gap-3">
        <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
        <p className="font-label text-label-md font-medium text-on-surface">
          Pipeline {status === "queued" ? "queued" : "running"}…
        </p>
        {elapsed && (
          <span className="ml-auto font-label text-label-sm text-secondary">
            {elapsed}
          </span>
        )}
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
