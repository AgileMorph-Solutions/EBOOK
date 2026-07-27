"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Label } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { AddVoiceModelDialog } from "@/components/audio/AddVoiceModelDialog";
import {
  listVoiceModels,
  type VoiceModel,
  type VoiceSelection,
} from "@/lib/api";

type VoiceModelPickerProps = {
  value: VoiceSelection | null;
  onChange: (selection: VoiceSelection | null) => void;
};

function toSelection(model: VoiceModel): VoiceSelection {
  return {
    voice_id: model.voice_id,
    model_id: model.model_id,
  };
}

function sortModels(rows: VoiceModel[]): VoiceModel[] {
  return [...rows].sort((a, b) => {
    if (a.is_default !== b.is_default) return a.is_default ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
}

export function VoiceModelPicker({ value, onChange }: VoiceModelPickerProps) {
  const [models, setModels] = useState<VoiceModel[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  const loadModels = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true);
    setError(null);
    try {
      const res = await listVoiceModels();
      const rows = sortModels(res.voice_models);
      setModels(rows);
      return rows;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load voices");
      return null;
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadModels().then((rows) => {
      if (!rows || rows.length === 0) return;
      const match = value
        ? rows.find(
            (m) =>
              m.voice_id === value.voice_id &&
              (m.model_id ?? null) === (value.model_id ?? null)
          )
        : null;
      const pick = match ?? rows.find((m) => m.is_default) ?? rows[0];
      setSelectedId(pick.id);
      onChange(toSelection(pick));
    });
    // Only initialize selection on first load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleSelectChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const id = e.target.value;
    setSelectedId(id);
    const model = models.find((m) => m.id === id);
    onChange(model ? toSelection(model) : null);
  }

  function handleCreated(created: VoiceModel) {
    setModels((prev) => {
      const without = prev.filter((m) => m.id !== created.id);
      return sortModels([...without, created]);
    });
    setSelectedId(created.id);
    onChange(toSelection(created));
    void loadModels({ silent: true });
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-end justify-between gap-3">
        <Label htmlFor="as-voice-model" className="flex-1">
          Narration voice
        </Label>
        <Button
          type="button"
          variant="outline"
          className="shrink-0 px-3 py-2 text-body-sm"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setAddOpen(true);
          }}
        >
          Add voice…
        </Button>
      </div>
      <Select
        id="as-voice-model"
        value={selectedId}
        disabled={loading || models.length === 0}
        onChange={handleSelectChange}
      >
        {loading && <option value="">Loading voices…</option>}
        {!loading && models.length === 0 && (
          <option value="">No voices saved — add one</option>
        )}
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
            {m.is_default ? " (default)" : ""} — {m.voice_id}
          </option>
        ))}
      </Select>
      {selectedId && (
        <p className="font-body text-body-sm italic text-secondary">
          {(() => {
            const m = models.find((x) => x.id === selectedId);
            if (!m) return null;
            return m.model_id
              ? `Model: ${m.model_id}`
              : "Model: server default (eleven_multilingual_v2)";
          })()}
        </p>
      )}
      {error && <Alert variant="error">{error}</Alert>}

      <AddVoiceModelDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={handleCreated}
      />
    </div>
  );
}
