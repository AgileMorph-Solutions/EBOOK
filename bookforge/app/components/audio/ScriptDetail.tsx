"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { RegenerateFeedbackDialog } from "@/components/audio/RegenerateFeedbackDialog";
import { ScriptCreationMeta } from "@/components/audio/ScriptCreationMeta";
import { Alert } from "@/components/ui/Alert";
import { Breadcrumb } from "@/components/ui/Breadcrumb";
import { Button } from "@/components/ui/Button";
import { GlassPanel } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Spinner";
import {
  downloadAudioScriptMp3,
  getApiUrl,
  type AudioScriptStatus,
} from "@/lib/api";
import { formatBookDate, shortBookId } from "@/lib/config";
import { slugify } from "@/lib/files";
import { cn } from "@/lib/cn";

const FALLBACK_STAGES = [
  { id: "brainstorming", label: "Brainstorm" },
  { id: "refining_list", label: "Refine list" },
  { id: "writing_items", label: "Write items" },
  { id: "complete", label: "Complete" },
];

type ScriptDetailProps = {
  scriptId: string;
  script: AudioScriptStatus | null;
  error: string | null;
  loading: boolean;
  isActive: boolean;
  refetch?: () => void | Promise<void>;
};

function visibleStages(script: AudioScriptStatus | null) {
  const raw = script?.stages?.length
    ? script.stages.map((s) => ({ id: s.id, label: s.label }))
    : FALLBACK_STAGES;
  const skipAudio = script?.skip_audio !== false;
  return raw.filter((s) => !(skipAudio && s.id === "generating_audio"));
}

function formatBytes(n?: number): string {
  if (n == null || !Number.isFinite(n) || n <= 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ScriptDetail({
  scriptId,
  script,
  error,
  loading,
  isActive,
  refetch,
}: ScriptDetailProps) {
  const [copied, setCopied] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [playUrl, setPlayUrl] = useState<string | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const stages = useMemo(() => visibleStages(script), [script]);
  const stageIndex = script?.stage
    ? stages.findIndex((s) => s.id === script.stage)
    : -1;

  const hasAudio = Boolean(script?.audio_link || script?.audio_key);
  const hasTranscript = Boolean(script?.transcript?.trim());
  const showResults =
    script?.status === "done" ||
    (script?.status === "failed" && hasTranscript);
  const audioFailedOnly =
    script?.status === "failed" && hasTranscript && !hasAudio;

  useEffect(() => {
    if (!hasAudio) {
      setPlayUrl(null);
      return;
    }
    let cancelled = false;
    void getApiUrl().then((base) => {
      if (!cancelled) {
        setPlayUrl(`${base}/audio-scripts/${scriptId}/audio`);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [hasAudio, scriptId]);

  async function handleCopy() {
    const text = script?.transcript?.trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  }

  function handleDownloadTranscript() {
    const text = script?.transcript?.trim();
    if (!text) return;
    const name = slugify(script?.title || scriptId);
    const blob = new Blob([text + "\n"], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.txt`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  async function handleDownload() {
    if (downloading) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const name = slugify(script?.title || scriptId);
      await downloadAudioScriptMp3(scriptId, `${name}.mp3`);
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  if (loading && !script) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-10 w-3/4" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  const subtitle = script?.created_at
    ? script.status === "done"
      ? `Generated on ${formatBookDate(script.created_at)}`
      : `Started ${formatBookDate(script.created_at)}`
    : undefined;

  return (
    <div className="space-y-8">
      <div>
        <Breadcrumb
          items={[
            { label: "Audio", href: "/audio-scripts" },
            { label: shortBookId(scriptId) },
          ]}
        />
        <div className="mt-4">
          <PageHeader
            title={script?.title ?? "Audio script"}
            status={script?.status}
            subtitle={subtitle}
          />
        </div>
      </div>

      {error && !script && <Alert variant="error">{error}</Alert>}

      {script && (
        <GlassPanel className="rounded-xl p-6 editorial-shadow">
          <h2 className="mb-4 font-display text-headline-sm text-on-surface">
            Script settings
          </h2>
          <ScriptCreationMeta script={script} />
        </GlassPanel>
      )}

      {isActive && script && (
        <GlassPanel className="rounded-xl p-6 editorial-shadow">
          <div className="flex items-center gap-3">
            <span className="h-2 w-2 animate-pulse rounded-full bg-primary" />
            <p className="font-label text-label-md font-medium text-on-surface">
              {script.status === "queued"
                ? "Queued…"
                : script.stage === "generating_audio"
                  ? "Generating narration audio…"
                  : "Writing transcript…"}
            </p>
            {script.progress && script.progress.total > 0 && (
              <span className="ml-auto font-label text-label-sm text-secondary tabular-nums">
                {script.progress.done}/{script.progress.total} items
              </span>
            )}
          </div>

          <ol className="mt-5 flex flex-wrap gap-2">
            {stages.map((s, i) => {
              const done =
                script.status === "done" ||
                (stageIndex >= 0 && i < stageIndex);
              const current = stageIndex === i;
              return (
                <li
                  key={s.id}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 font-label text-label-sm",
                    done &&
                      "border-tertiary/30 bg-tertiary/10 text-tertiary",
                    current &&
                      "border-primary/40 bg-primary/15 text-primary",
                    !done &&
                      !current &&
                      "border-white/[0.06] bg-surface-container-low text-secondary"
                  )}
                >
                  {done && <Icon name="check" size={14} />}
                  {current && (
                    <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
                  )}
                  {s.label}
                </li>
              );
            })}
          </ol>
        </GlassPanel>
      )}

      {script?.status === "failed" && (
        <Alert variant="error">
          <p className="font-label text-label-md font-semibold">
            {audioFailedOnly
              ? "Audio generation failed"
              : "Generation failed"}
          </p>
          <p className="mt-1 font-body text-body-sm">
            {script.error ?? "Something went wrong during script generation."}
          </p>
          {script.error_detail && (
            <p className="mt-2 font-body text-body-sm text-secondary">
              {script.error_detail}
            </p>
          )}
          {audioFailedOnly && (
            <p className="mt-2 font-body text-body-sm text-on-surface">
              The transcript was written successfully — you can copy or download
              it below. Fix the voice preset and regenerate to create audio.
            </p>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button
              variant="surface"
              type="button"
              onClick={() => setFeedbackOpen(true)}
            >
              <Icon name="refresh" size={16} />
              Regenerate
            </Button>
            <Link href="/audio-scripts">
              <Button variant="outline" type="button">
                Back to create
              </Button>
            </Link>
          </div>
          <p className="mt-3 font-label text-label-sm text-secondary">
            Regenerate rewrites the full transcript and audio using your
            feedback, with the same title and settings.
          </p>
        </Alert>
      )}

      {showResults && (
        <div className="space-y-6">
          {hasAudio && (
            <GlassPanel className="rounded-xl p-6 editorial-shadow">
              <div className="flex flex-wrap items-center gap-3">
                <Icon name="graphic_eq" className="text-primary" size={22} />
                <div>
                  <h2 className="font-display text-headline-sm text-on-surface">
                    Narration
                  </h2>
                  <p className="font-label text-label-sm text-secondary">
                    Play in browser or download the MP3
                    {script.audio_size_bytes
                      ? ` · ${formatBytes(script.audio_size_bytes)}`
                      : ""}
                  </p>
                </div>
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="surface"
                    onClick={() => setFeedbackOpen(true)}
                  >
                    <Icon name="refresh" size={16} />
                    Regenerate
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={handleDownload}
                    disabled={downloading}
                  >
                    <Icon
                      name={downloading ? "hourglass_top" : "download"}
                      size={16}
                    />
                    {downloading ? "Downloading…" : "Download MP3"}
                  </Button>
                </div>
              </div>

              {downloadError && (
                <Alert variant="error" className="mt-4">
                  {downloadError}
                </Alert>
              )}

              {playUrl && (
                <audio
                  key={playUrl}
                  controls
                  preload="metadata"
                  className="mt-5 w-full"
                  src={playUrl}
                >
                  Your browser does not support audio playback.
                </audio>
              )}
            </GlassPanel>
          )}

          <GlassPanel className="rounded-xl p-6">
            <div className="flex flex-wrap items-center gap-4 font-label text-label-sm text-secondary">
              {script.category && (
                <span className="capitalize">{script.category}</span>
              )}
              {script.item_count != null && (
                <span>{script.item_count} items</span>
              )}
              {script.total_words != null && (
                <span>{script.total_words} words</span>
              )}
              <span className="text-outline">
                {hasAudio
                  ? "Transcript + audio"
                  : audioFailedOnly
                    ? "Transcript only (audio failed)"
                    : "Transcript only"}
              </span>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                {!hasAudio && (
                  <Button
                    type="button"
                    variant="surface"
                    onClick={() => setFeedbackOpen(true)}
                  >
                    <Icon name="refresh" size={16} />
                    Regenerate
                  </Button>
                )}
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleDownloadTranscript}
                  disabled={!script.transcript}
                >
                  <Icon name="download" size={16} />
                  Download .txt
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleCopy}
                  disabled={!script.transcript}
                >
                  <Icon name={copied ? "check" : "content_copy"} size={16} />
                  {copied ? "Copied" : "Copy transcript"}
                </Button>
              </div>
            </div>

            {script.transcript && (
              <pre className="custom-scrollbar mt-6 max-h-[28rem] overflow-y-auto whitespace-pre-wrap rounded-lg border border-white/[0.06] bg-surface-container-lowest p-4 font-body text-body-md leading-relaxed text-on-surface">
                {script.transcript}
              </pre>
            )}
          </GlassPanel>

          {script.items && script.items.length > 0 && (
            <div className="space-y-4">
              <h2 className="font-display text-headline-sm text-on-surface">
                Items
              </h2>
              {script.items.map((item) => (
                <GlassPanel key={item.number} className="rounded-xl p-5">
                  <div className="flex items-baseline justify-between gap-3">
                    <h3 className="font-body text-body-lg font-semibold text-on-surface">
                      <span className="mr-2 font-label text-label-sm text-primary">
                        {item.number}.
                      </span>
                      {item.title}
                    </h3>
                    <span className="shrink-0 font-label text-label-sm text-outline">
                      {item.word_count} words
                    </span>
                  </div>
                  <p className="mt-3 whitespace-pre-wrap font-body text-body-md leading-relaxed text-on-surface-variant">
                    {item.body}
                  </p>
                </GlassPanel>
              ))}
            </div>
          )}
        </div>
      )}

      <RegenerateFeedbackDialog
        scriptId={scriptId}
        open={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
        onSuccess={() => {
          void refetch?.();
        }}
      />
    </div>
  );
}
