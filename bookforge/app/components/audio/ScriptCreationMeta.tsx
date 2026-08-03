"use client";

import { useEffect, useState } from "react";
import { listVoiceModels, type VoiceModel } from "@/lib/api";
import {
  formatDurationLabel,
  formatOutputMode,
  formatWordsPerParagraph,
  type ScriptMetaInput,
} from "@/components/audio/scriptMeta";

type ScriptCreationMetaProps = {
  script: ScriptMetaInput;
  compact?: boolean;
};

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
      <dt className="shrink-0 font-label text-label-sm text-secondary sm:w-36">
        {label}
      </dt>
      <dd className="font-body text-body-sm text-on-surface">{value}</dd>
    </div>
  );
}

export function ScriptCreationMeta({
  script,
  compact = false,
}: ScriptCreationMetaProps) {
  const [voiceModels, setVoiceModels] = useState<VoiceModel[]>([]);

  useEffect(() => {
    void listVoiceModels()
      .then((res) => setVoiceModels(res.voice_models))
      .catch(() => setVoiceModels([]));
  }, []);

  const voiceId = script.voice_id ?? script.creation?.voice_id ?? null;
  const modelId = script.model_id ?? script.creation?.model_id ?? null;
  const voiceName = voiceId
    ? voiceModels.find((m) => m.voice_id === voiceId)?.name ??
      `${voiceId.slice(0, 8)}…`
    : null;

  // paragraph_count (explicit) overrides title — show as "override" when set
  const explicitParagraphCount =
    script.paragraph_count ?? script.creation?.paragraph_count ?? null;
  const actualItemCount = script.item_count ?? null;

  const paragraphLabel = explicitParagraphCount != null
    ? `${explicitParagraphCount} (override)`
    : actualItemCount != null
      ? `${actualItemCount} (from title)`
      : null;

  const duration = script.target_duration_minutes ?? null;

  if (compact) {
    const chips = [
      script.category,
      formatWordsPerParagraph(script),
      explicitParagraphCount != null ? `${explicitParagraphCount} paragraphs` : null,
      formatOutputMode(script),
    ].filter(Boolean);
    return (
      <div className="flex flex-wrap gap-2">
        {chips.map((chip) => (
          <span
            key={chip}
            className="rounded-full border border-white/[0.06] bg-surface-container-low px-2.5 py-1 font-label text-label-sm text-on-surface-variant capitalize"
          >
            {chip}
          </span>
        ))}
      </div>
    );
  }

  return (
    <dl className="space-y-3">
      {script.category && (
        <MetaRow label="Category" value={script.category} />
      )}
      <MetaRow label="Words per paragraph" value={formatWordsPerParagraph(script)} />
      {paragraphLabel && (
        <MetaRow label="Total paragraphs" value={paragraphLabel} />
      )}
      {duration != null && duration > 0 && (
        <MetaRow
          label="Duration target"
          value={formatDurationLabel(duration)}
        />
      )}
      <MetaRow label="Output" value={formatOutputMode(script)} />
      {voiceName && <MetaRow label="Voice" value={voiceName} />}
      {modelId && <MetaRow label="TTS model" value={modelId} />}
    </dl>
  );
}
