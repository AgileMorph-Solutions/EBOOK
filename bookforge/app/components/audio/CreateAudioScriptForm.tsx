"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input, Label } from "@/components/ui/Input";
import { DurationConflictDialog } from "@/components/audio/DurationConflictDialog";
import { VoiceModelPicker } from "@/components/audio/VoiceModelPicker";
import {
  createAudioScript,
  estimateAudioScript,
  type AudioScriptDurationEstimate,
  type CreateAudioScriptRequest,
  type VoiceSelection,
} from "@/lib/api";
import { cn } from "@/lib/cn";

export function CreateAudioScriptForm() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("psychology");
  const [totalTranscriptWords, setTotalTranscriptWords] = useState("");
  const [generateAudio, setGenerateAudio] = useState(true);
  const [voiceSelection, setVoiceSelection] = useState<VoiceSelection | null>(
    null
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflictEstimate, setConflictEstimate] =
    useState<AudioScriptDurationEstimate | null>(null);

  const canSubmit =
    title.trim().length > 0 && category.trim().length > 0 && !submitting;

  function parseOverallCap(): number | null {
    const overall = totalTranscriptWords
      ? parseInt(totalTranscriptWords, 10)
      : null;
    if (overall != null && Number.isFinite(overall) && overall >= 100) {
      return overall;
    }
    return null;
  }

  function basePayload(): CreateAudioScriptRequest {
    const payload: CreateAudioScriptRequest = {
      title: title.trim(),
      category: category.trim(),
      skip_audio: !generateAudio,
    };
    if (generateAudio && voiceSelection) {
      payload.voice_id = voiceSelection.voice_id;
      if (voiceSelection.model_id) {
        payload.model_id = voiceSelection.model_id;
      }
    }
    return payload;
  }

  async function startJob(payload: CreateAudioScriptRequest) {
    setError(null);
    setSubmitting(true);
    try {
      const res = await createAudioScript(payload);
      router.push(`/audio-scripts/${res.script_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
      setSubmitting(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    const overall = parseOverallCap();
    if (overall != null) {
      try {
        const estimate = await estimateAudioScript({
          title: title.trim(),
          overall_word_limit: overall,
        });
        if (estimate.conflict) {
          setConflictEstimate(estimate);
          return;
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not analyze title");
        return;
      }
    }

    await startJob({
      ...basePayload(),
      overall_word_limit: overall,
    });
  }

  async function handleMatchTitle() {
    if (!canSubmit) return;
    setConflictEstimate(null);
    await startJob(basePayload());
  }

  async function handleKeepShort() {
    if (!canSubmit) return;
    const overall = parseOverallCap();
    if (overall == null) return;
    setConflictEstimate(null);
    await startJob({
      ...basePayload(),
      overall_word_limit: overall,
      accept_short_duration: true,
    });
  }

  return (
    <>
      <form onSubmit={handleSubmit} className="space-y-8">
        <div className="flex flex-col gap-2">
          <Label htmlFor="as-title">
            Narration title <span className="text-primary">*</span>
          </Label>
          <Input
            id="as-title"
            variant="underline"
            type="text"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. 30 Dark Psychology Tricks That Actually Work"
          />
          <p className="font-body text-body-sm italic text-secondary">
            Include an item count (e.g. “100 ways…”) or a listening time (e.g.
            “2 Minutes of…”) — otherwise defaults to ~1000 words split across
            automatically sized sections. Title duration takes priority over a
            lower word cap unless you confirm otherwise.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="as-category">
            Category <span className="text-primary">*</span>
          </Label>
          <Input
            id="as-category"
            type="text"
            required
            maxLength={80}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="e.g. psychology, true crime, stoicism"
          />
          <p className="font-body text-body-sm italic text-secondary">
            Any niche — used to steer the narration voice (not limited to a fixed
            list).
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="as-total-words">
            Total transcript words{" "}
            <span className="font-normal text-secondary">(optional)</span>
          </Label>
          <Input
            id="as-total-words"
            type="number"
            min={100}
            max={150000}
            placeholder="Leave blank for ~1000 words"
            value={totalTranscriptWords}
            onChange={(e) => setTotalTranscriptWords(e.target.value)}
          />
          <p className="font-body text-body-sm italic text-secondary">
            Cap for the whole transcript. Each numbered section is sized
            automatically from this total (total ÷ section count). Leave blank
            for ~1000 words when the title has no count or duration.
          </p>
        </div>

        {generateAudio && (
          <VoiceModelPicker
            value={voiceSelection}
            onChange={setVoiceSelection}
          />
        )}

        <label
          htmlFor="as-generate-audio"
          className={cn(
            "flex cursor-pointer items-start gap-3 rounded-xl border p-4 transition-colors",
            generateAudio
              ? "border-primary/30 bg-primary/[0.06]"
              : "border-white/[0.06] bg-surface-container-low"
          )}
        >
          <input
            id="as-generate-audio"
            type="checkbox"
            checked={generateAudio}
            onChange={(e) => setGenerateAudio(e.target.checked)}
            className="mt-1 h-4 w-4 accent-primary"
          />
          <span>
            <span className="block font-label text-label-md font-semibold text-on-surface">
              Generate narration audio (TTS)
            </span>
            <span className="mt-1 block font-body text-body-sm text-secondary">
              After the transcript is written, synthesize a single MP3 with
              ElevenLabs. Uses paid TTS credits — turn off for transcript only.
            </span>
          </span>
        </label>

        {error && <Alert variant="error">{error}</Alert>}

        <div className="pt-2">
          <Button
            type="submit"
            disabled={!canSubmit}
            className="w-full px-10 py-4 font-display text-headline-sm sm:w-auto"
          >
            {submitting
              ? "Starting…"
              : generateAudio
                ? "Generate script & audio"
                : "Generate transcript"}
            {!submitting && (
              <Icon name={generateAudio ? "graphic_eq" : "mic"} />
            )}
          </Button>
        </div>
      </form>

      {conflictEstimate && (
        <DurationConflictDialog
          open
          estimate={conflictEstimate}
          submitting={submitting}
          onClose={() => {
            if (!submitting) setConflictEstimate(null);
          }}
          onMatchTitle={handleMatchTitle}
          onKeepShort={handleKeepShort}
        />
      )}
    </>
  );
}
