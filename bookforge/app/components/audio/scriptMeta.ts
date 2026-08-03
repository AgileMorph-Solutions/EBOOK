import type { AudioScriptStatus, AudioScriptSummary } from "@/lib/api";

export type ScriptMetaInput = {
  category?: string | null;
  word_count?: number | null;
  effective_word_count?: number | null;
  paragraph_count?: number | null;
  item_count?: number | null;
  item_limit?: number | null;
  target_duration_minutes?: number | null;
  skip_audio?: boolean | null;
  voice_id?: string | null;
  model_id?: string | null;
  creation?: AudioScriptStatus["creation"];
};

export function formatDurationLabel(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours && mins) return `${hours}h ${mins}m`;
  if (hours) return `${hours} hour${hours === 1 ? "" : "s"}`;
  return `${mins} minute${mins === 1 ? "" : "s"}`;
}

export function formatWordsPerParagraph(script: ScriptMetaInput): string {
  const raw =
    script.word_count ??
    script.effective_word_count ??
    script.creation?.word_count ??
    null;
  if (raw != null) return `${raw} words/paragraph`;
  return "265–270 words/paragraph";
}

export function formatOutputMode(script: ScriptMetaInput): string {
  const skip =
    script.skip_audio ?? script.creation?.skip_audio ?? false;
  return skip ? "Transcript only" : "Transcript + audio";
}

export function compactSidebarMeta(script: AudioScriptSummary): string {
  const parts: string[] = [];
  if (script.category) parts.push(script.category);
  parts.push(formatWordsPerParagraph(script));
  if (script.paragraph_count != null) {
    parts.push(`${script.paragraph_count} ¶`);
  }
  parts.push(script.has_audio ? "Narration" : "Transcript");
  return parts.join(" · ");
}
