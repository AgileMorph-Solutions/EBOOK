"use client";

import { useEffect, useId } from "react";
import { Button } from "@/components/ui/Button";
import { GlassPanel } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import type { AudioScriptDurationEstimate } from "@/lib/api";

type DurationConflictDialogProps = {
  open: boolean;
  estimate: AudioScriptDurationEstimate;
  submitting: boolean;
  onClose: () => void;
  onMatchTitle: () => void;
  onKeepShort: () => void;
};

function formatDuration(minutes: number): string {
  const totalSeconds = Math.round(minutes * 60);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function DurationConflictDialog({
  open,
  estimate,
  submitting,
  onClose,
  onMatchTitle,
  onKeepShort,
}: DurationConflictDialogProps) {
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !submitting) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, submitting, onClose]);

  if (!open) return null;

  const titleMinutes = estimate.title_duration_minutes ?? 0;
  const capMinutes = estimate.estimated_minutes_from_cap ?? 0;
  const titleLabel = formatDuration(titleMinutes);
  const capLabel = formatDuration(capMinutes);
  const capWords = estimate.overall_word_limit ?? 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        aria-label="Close dialog"
        disabled={submitting}
        onClick={() => {
          if (!submitting) onClose();
        }}
      />
      <GlassPanel className="relative z-10 w-full max-w-lg rounded-2xl p-6 editorial-shadow">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2
              id={titleId}
              className="font-display text-headline-sm text-on-surface"
            >
              Word cap is too low for title duration
            </h2>
            <p className="mt-2 font-body text-body-sm text-secondary">
              {estimate.reason}
            </p>
          </div>
          <button
            type="button"
            className="rounded-lg p-1.5 text-secondary hover:bg-white/[0.06] hover:text-on-surface disabled:opacity-40"
            aria-label="Close"
            disabled={submitting}
            onClick={onClose}
          >
            <Icon name="close" size={18} />
          </button>
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-end gap-3">
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onKeepShort}
          >
            Keep {capWords.toLocaleString()} words (~{capLabel})
          </Button>
          <Button type="button" disabled={submitting} onClick={onMatchTitle}>
            {submitting ? "Starting…" : `Match title (~${titleLabel})`}
          </Button>
        </div>
      </GlassPanel>
    </div>
  );
}
