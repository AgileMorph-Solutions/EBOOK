"use client";

import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { GlassPanel } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Input, Label } from "@/components/ui/Input";
import { createVoiceModel, type VoiceModel } from "@/lib/api";

type AddVoiceModelDialogProps = {
  open: boolean;
  onClose: () => void;
  onCreated: (voice: VoiceModel) => void;
};

export function AddVoiceModelDialog({
  open,
  onClose,
  onCreated,
}: AddVoiceModelDialogProps) {
  const titleId = useId();
  const [name, setName] = useState("");
  const [voiceId, setVoiceId] = useState("");
  const [modelId, setModelId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName("");
    setVoiceId("");
    setModelId("");
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

  const canSubmit =
    name.trim().length > 0 && voiceId.trim().length > 0 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const created = await createVoiceModel({
        name: name.trim(),
        voice_id: voiceId.trim(),
        model_id: modelId.trim() || null,
      });
      onCreated(created);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save voice");
      setSubmitting(false);
    }
  }

  return createPortal(
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
              Add voice preset
            </h2>
            <p className="mt-1 font-body text-body-sm text-secondary">
              Save an ElevenLabs voice ID (and optional model ID) for reuse.
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
          <div className="flex flex-col gap-2">
            <Label htmlFor="vm-name">Name</Label>
            <Input
              id="vm-name"
              type="text"
              required
              maxLength={120}
              value={name}
              disabled={submitting}
              autoFocus
              placeholder="e.g. British narrator"
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="vm-voice-id">ElevenLabs voice ID</Label>
            <Input
              id="vm-voice-id"
              type="text"
              required
              maxLength={80}
              value={voiceId}
              disabled={submitting}
              placeholder="e.g. bP8FJDHmWVEgXJDitdQd"
              onChange={(e) => setVoiceId(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="vm-model-id">
              Model ID{" "}
              <span className="font-normal text-secondary">(optional)</span>
            </Label>
            <Input
              id="vm-model-id"
              type="text"
              maxLength={80}
              value={modelId}
              disabled={submitting}
              placeholder="eleven_multilingual_v2"
              onChange={(e) => setModelId(e.target.value)}
            />
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
            <Button type="submit" disabled={!canSubmit}>
              {submitting ? "Saving…" : "Save voice"}
            </Button>
          </div>
        </form>
      </GlassPanel>
    </div>,
    document.body
  );
}
