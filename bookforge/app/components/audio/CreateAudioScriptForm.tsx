"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input, Label } from "@/components/ui/Input";
import { VoiceModelPicker } from "@/components/audio/VoiceModelPicker";
import {
  createAudioScript,
  type CreateAudioScriptRequest,
  type VoiceSelection,
} from "@/lib/api";
import { cn } from "@/lib/cn";

export function CreateAudioScriptForm() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("psychology");
  const [wordsPerSection, setWordsPerSection] = useState("");
  const [totalParagraphs, setTotalParagraphs] = useState("");
  const [generateAudio, setGenerateAudio] = useState(true);
  const [voiceSelection, setVoiceSelection] = useState<VoiceSelection | null>(
    null
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit =
    title.trim().length > 0 && category.trim().length > 0 && !submitting;

  function parseWordCount(): number | null {
    const count = wordsPerSection ? parseInt(wordsPerSection, 10) : null;
    if (count != null && Number.isFinite(count) && count >= 100 && count <= 500) {
      return count;
    }
    return null;
  }

  function parseParagraphCount(): number | null {
    const count = totalParagraphs ? parseInt(totalParagraphs, 10) : null;
    if (count != null && Number.isFinite(count) && count >= 1) {
      return count;
    }
    return null;
  }

  function buildPayload(): CreateAudioScriptRequest {
    const payload: CreateAudioScriptRequest = {
      title: title.trim(),
      category: category.trim(),
      skip_audio: !generateAudio,
    };
    const wordCount = parseWordCount();
    if (wordCount != null) {
      payload.word_count = wordCount;
    }
    const paragraphCount = parseParagraphCount();
    if (paragraphCount != null) {
      payload.paragraph_count = paragraphCount;
    }
    if (generateAudio && voiceSelection) {
      payload.voice_id = voiceSelection.voice_id;
      if (voiceSelection.model_id) {
        payload.model_id = voiceSelection.model_id;
      }
    }
    return payload;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setError(null);
    setSubmitting(true);
    try {
      const res = await createAudioScript(buildPayload());
      router.push(`/audio-scripts/${res.script_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
      setSubmitting(false);
    }
  }

  return (
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
          “3 Hours of…”) to set how many sections are written. Each section is
          sized to the word band below.
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
        <Label htmlFor="as-word-count">
          Words per section{" "}
          <span className="font-normal text-secondary">(optional)</span>
        </Label>
        <Input
          id="as-word-count"
          type="number"
          min={100}
          max={500}
          placeholder="Leave blank for 265–270 (default)"
          value={wordsPerSection}
          onChange={(e) => setWordsPerSection(e.target.value)}
        />
        <p className="font-body text-body-sm italic text-secondary">
          Target word count for each numbered section. Leave blank to use the
          default band of 265–270 words per section.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="as-paragraph-count">
          Total paragraphs{" "}
          <span className="font-normal text-secondary">(optional)</span>
        </Label>
        <Input
          id="as-paragraph-count"
          type="number"
          min={1}
          max={5000}
          placeholder="Leave blank for natural breaks"
          value={totalParagraphs}
          onChange={(e) => setTotalParagraphs(e.target.value)}
        />
        <p className="font-body text-body-sm italic text-secondary">
          Total paragraphs across the full transcript, split across all sections.
          Must be at least the section count. Leave blank for natural paragraph
          breaks.
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
  );
}
