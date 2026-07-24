"use client";

import { useEffect, useId, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { GlassPanel } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Label, Textarea } from "@/components/ui/Input";
import { regenerateAudioScript } from "@/lib/api";

type RegenerateFeedbackDialogProps = {
  scriptId: string;
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
};

const MAX_FEEDBACK = 2000;

export function RegenerateFeedbackDialog({
  scriptId,
  open,
  onClose,
  onSuccess,
}: RegenerateFeedbackDialogProps) {
  const titleId = useId();
  const [feedback, setFeedback] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setFeedback("");
    setError(null);
    setSubmitting(false);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !submitting) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, submitting, onClose]);

  if (!open) return null;

  const trimmed = feedback.trim();
  const canSubmit = trimmed.length > 0 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await regenerateAudioScript(scriptId, { feedback: trimmed });
      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Regenerate failed");
      setSubmitting(false);
    }
  }

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
              Regenerate with feedback
            </h2>
            <p className="mt-1 font-body text-body-sm text-secondary">
              Describe what to change. The full transcript and audio will be
              rewritten using your notes.
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

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div>
            <Label htmlFor="regen-feedback">Feedback</Label>
            <Textarea
              id="regen-feedback"
              className="mt-2 min-h-[9rem]"
              placeholder="e.g. Make openings more scene-based, drop abstract definitions, keep a calmer late-night tone…"
              value={feedback}
              maxLength={MAX_FEEDBACK}
              disabled={submitting}
              autoFocus
              onChange={(e) => setFeedback(e.target.value)}
            />
            <p className="mt-1.5 text-right font-label text-label-sm text-outline tabular-nums">
              {trimmed.length}/{MAX_FEEDBACK}
            </p>
          </div>

          {error && <Alert variant="error">{error}</Alert>}

          <div className="flex flex-wrap items-center justify-end gap-3">
            <Button
              type="button"
              variant="outline"
              disabled={submitting}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" variant="surface" disabled={!canSubmit}>
              <Icon
                name={submitting ? "hourglass_top" : "refresh"}
                size={16}
              />
              {submitting ? "Regenerating…" : "Regenerate"}
            </Button>
          </div>
        </form>
      </GlassPanel>
    </div>
  );
}
