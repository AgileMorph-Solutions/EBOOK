"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input, Label } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import {
  createAudioScript,
  type AudioScriptCategory,
} from "@/lib/api";
import { cn } from "@/lib/cn";

const CATEGORIES: { value: AudioScriptCategory; label: string }[] = [
  { value: "psychology", label: "Psychology" },
  { value: "relationship", label: "Relationship" },
  { value: "philosophy", label: "Philosophy" },
  { value: "general", label: "General" },
];

export function CreateAudioScriptForm() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState<AudioScriptCategory>("psychology");
  const [itemLimit, setItemLimit] = useState("");
  const [wordCount, setWordCount] = useState("");
  const [generateAudio, setGenerateAudio] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = title.trim().length > 0 && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setError(null);
    setSubmitting(true);

    try {
      const limit = itemLimit ? parseInt(itemLimit, 10) : null;
      const words = wordCount ? parseInt(wordCount, 10) : null;

      const res = await createAudioScript({
        title: title.trim(),
        category,
        item_limit:
          limit != null && Number.isFinite(limit) && limit > 0 ? limit : null,
        word_count:
          words != null && Number.isFinite(words) && words >= 100
            ? words
            : null,
        skip_audio: !generateAudio,
      });
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
          Include the item count in the title when you can — it drives how many
          concepts get written.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="as-category">Category</Label>
        <Select
          id="as-category"
          value={category}
          onChange={(e) =>
            setCategory(e.target.value as AudioScriptCategory)
          }
        >
          {CATEGORIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </Select>
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor="as-items">
            Item limit{" "}
            <span className="font-normal text-secondary">(optional)</span>
          </Label>
          <Input
            id="as-items"
            type="number"
            min={1}
            max={100}
            placeholder="e.g. 3 for a cheap test"
            value={itemLimit}
            onChange={(e) => setItemLimit(e.target.value)}
          />
          <p className="font-body text-body-sm italic text-secondary">
            Caps how many items to write. Leave blank for the full title count.
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="as-words">
            Words per item{" "}
            <span className="font-normal text-secondary">(optional)</span>
          </Label>
          <Input
            id="as-words"
            type="number"
            min={100}
            max={500}
            placeholder="default 265–270"
            value={wordCount}
            onChange={(e) => setWordCount(e.target.value)}
          />
        </div>
      </div>

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
