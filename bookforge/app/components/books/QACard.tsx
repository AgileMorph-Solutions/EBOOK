import type { QAResult } from "@/lib/api";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { ProgressBar } from "@/components/ui/ProgressBar";

type QualityAnalysisCardProps = {
  qa: QAResult;
};

export function QualityAnalysisCard({ qa }: QualityAnalysisCardProps) {
  const unsupported = qa.unsupported_claims ?? [];
  const groundednessPct = Math.round(qa.groundedness * 100);
  const proseOutOf10 = (qa.prose_quality_score * 10).toFixed(1);

  return (
    <Card variant="elevated" className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <h3 className="font-display text-headline-sm text-on-surface">
          Quality Analysis
        </h3>
        {qa.passed && <Badge variant="verified">Verified</Badge>}
      </div>

      <div className="space-y-6">
        <div>
          <div className="mb-2 flex items-end justify-between">
            <span className="font-label text-label-md text-on-surface">
              Groundedness
            </span>
            <span className="font-label text-label-md font-bold text-tertiary">
              {groundednessPct}% {qa.passed ? "Pass" : "Review"}
            </span>
          </div>
          <ProgressBar value={groundednessPct} color="tertiary" />
          <p className="mt-2 text-[11px] leading-relaxed text-outline">
            Cross-referenced against cited source passages.
          </p>
        </div>

        <div className="border-t border-outline/5 pt-4">
          <div className="mb-2 flex items-end justify-between">
            <span className="font-label text-label-md text-on-surface">
              Prose Clarity
            </span>
            <span className="font-label text-label-md text-on-surface">
              {proseOutOf10}/10
            </span>
          </div>
          <ProgressBar
            value={qa.prose_quality_score * 100}
            color="primary"
          />
        </div>

        <div className="flex items-center justify-between rounded-lg border border-tertiary/10 bg-tertiary/5 px-4 py-3">
          <div className="flex items-center gap-2">
            <Icon name="verified" filled className="text-tertiary" />
            <span className="font-label text-label-md text-on-surface">
              Unsupported Claims
            </span>
          </div>
          <span className="font-display text-headline-sm text-tertiary">
            {unsupported.length}
          </span>
        </div>

        {unsupported.length > 0 && (
          <details>
            <summary className="cursor-pointer font-body text-body-sm text-secondary hover:text-on-surface">
              View unsupported claims
            </summary>
            <ul className="mt-2 list-inside list-disc space-y-1 font-body text-body-sm text-secondary">
              {unsupported.slice(0, 10).map((claim, i) => (
                <li key={i}>
                  {typeof claim === "string" ? claim : JSON.stringify(claim)}
                </li>
              ))}
              {unsupported.length > 10 && (
                <li>…and {unsupported.length - 10} more</li>
              )}
            </ul>
          </details>
        )}

        {(qa.layout_issues?.length ?? 0) > 0 && (
          <p className="font-body text-body-sm text-primary">
            Layout issues: {qa.layout_issues!.length}
          </p>
        )}
      </div>
    </Card>
  );
}

/** @deprecated Use QualityAnalysisCard */
export function QACard({ qa }: { qa: QAResult }) {
  return <QualityAnalysisCard qa={qa} />;
}
