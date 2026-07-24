"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  downloadAudioScriptMp3,
  listAudioScripts,
  type AudioScriptSummary,
} from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { Alert } from "@/components/ui/Alert";
import { StatusBadge } from "@/components/ui/Badge";
import { GlassPanel } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Skeleton } from "@/components/ui/Spinner";
import { slugify } from "@/lib/files";
import { cn } from "@/lib/cn";

function formatRelative(iso: string) {
  if (!iso) return "—";
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const min = Math.floor(diff / 60000);
    if (min < 1) return "just now";
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return new Date(iso).toLocaleDateString("en-GB");
  } catch {
    return iso;
  }
}

function ScriptCard({ script }: { script: AudioScriptSummary }) {
  const isRunning =
    script.status === "running" || script.status === "queued";
  const isDone = script.status === "done";
  const hasAudio = Boolean(script.has_audio);
  const [downloading, setDownloading] = useState(false);

  async function handleDownload(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (downloading || !hasAudio) return;
    setDownloading(true);
    try {
      await downloadAudioScriptMp3(
        script.script_id,
        `${slugify(script.title || script.script_id)}.mp3`
      );
    } catch {
      /* surfaced on the detail page */
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Link
      href={`/audio-scripts/${script.script_id}`}
      className={cn(
        "group relative block cursor-pointer overflow-hidden rounded-xl p-4 transition-all",
        isRunning
          ? "border border-primary/25 bg-primary/[0.06] editorial-shadow hover:bg-primary/10"
          : "border border-white/[0.05] bg-surface-container-low hover:border-white/10 hover:bg-surface-container",
        !isRunning && !isDone && "opacity-90"
      )}
    >
      {isRunning && (
        <div className="absolute bottom-0 left-0 top-0 w-1 bg-primary" />
      )}
      <div className="flex flex-col gap-1">
        <div className="flex items-start justify-between gap-2">
          <h3 className="truncate pr-1 font-body text-body-md font-semibold text-on-surface">
            {script.title || script.script_id}
          </h3>
          <div className="flex shrink-0 items-center gap-1.5">
            {isDone && hasAudio && (
              <button
                type="button"
                onClick={handleDownload}
                disabled={downloading}
                title="Download MP3"
                aria-label="Download MP3"
                className="flex h-7 w-7 items-center justify-center rounded-md text-primary transition-colors hover:bg-primary/15 disabled:opacity-50"
              >
                <Icon
                  name={downloading ? "hourglass_top" : "download"}
                  size={16}
                />
              </button>
            )}
            <StatusBadge status={script.status} />
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Icon
              name={hasAudio ? "graphic_eq" : "mic"}
              className="text-tertiary"
              size={14}
            />
            <span className="font-body text-body-sm text-secondary">
              {hasAudio ? "Narration" : "Transcript"}
            </span>
          </div>
          <span className="font-label text-label-sm text-outline">
            {formatRelative(script.created_at)}
          </span>
        </div>
      </div>
    </Link>
  );
}

export function ScriptSidebar() {
  const [scripts, setScripts] = useState<AudioScriptSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchScripts = useCallback(async () => {
    try {
      const data = await listAudioScripts();
      setScripts(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  const hasActive = scripts.some(
    (s) => s.status === "queued" || s.status === "running"
  );

  useEffect(() => {
    void fetchScripts();
  }, [fetchScripts]);

  usePolling(fetchScripts, {
    enabled: hasActive,
    intervalMs: 5000,
    maxIntervalMs: 10000,
    backoffFactor: 1,
  });

  return (
    <GlassPanel className="sticky top-28 flex h-[calc(100vh-160px)] flex-col rounded-2xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <h2 className="font-display text-headline-sm text-on-surface">
          Recent scripts
        </h2>
        <Icon name="history" className="text-secondary" />
      </div>

      <div className="custom-scrollbar flex-1 space-y-3 overflow-y-auto pr-2">
        {loading && (
          <>
            {[1, 2, 3].map((i) => (
              <div key={i} className="rounded-xl p-4">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="mt-2 h-3 w-1/2" />
              </div>
            ))}
          </>
        )}

        {error && (
          <Alert variant="error">Could not reach API: {error}</Alert>
        )}

        {!loading && !error && scripts.length === 0 && (
          <p className="font-body text-body-sm text-secondary">
            No scripts yet. Create your first transcript using the form.
          </p>
        )}

        {!loading &&
          !error &&
          scripts.map((script) => (
            <ScriptCard key={script.script_id} script={script} />
          ))}
      </div>
    </GlassPanel>
  );
}
