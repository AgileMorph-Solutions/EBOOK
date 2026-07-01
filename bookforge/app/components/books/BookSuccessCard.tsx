"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Spinner } from "@/components/ui/Spinner";
import { downloadBookPdf, getApiUrl } from "@/lib/api";
import {
  formatTrimSize,
  getAppConfig,
  type AppConfig,
} from "@/lib/config";
import { slugify } from "@/lib/files";
import type { BookArtifact } from "@/lib/api";

type BookSuccessCardProps = {
  bookId: string;
  topic: string;
  artifact: BookArtifact;
};

export function BookSuccessCard({
  bookId,
  topic,
  artifact,
}: BookSuccessCardProps) {
  const [downloading, setDownloading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    void getAppConfig().then(setConfig);
  }, []);

  async function handleDownload() {
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadBookPdf(bookId, `${slugify(topic)}.pdf`);
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  async function handlePreview() {
    setPreviewing(true);
    try {
      const apiUrl = await getApiUrl();
      window.open(`${apiUrl}/books/${bookId}/download`, "_blank");
    } finally {
      setPreviewing(false);
    }
  }

  const trimLabel = config
    ? formatTrimSize(config.trimWidthIn, config.trimHeightIn)
    : '8.5" × 11"';

  return (
    <div className="glass-panel success-accent-border relative overflow-hidden rounded-xl p-8 editorial-shadow">
      <div className="absolute right-0 top-0 p-4 opacity-10">
        <Icon name="auto_stories" size={96} />
      </div>
      <div className="relative z-10">
        <h2 className="mb-4 font-display text-headline-md text-on-surface">
          Your ebook is ready
        </h2>
        <p className="mb-8 max-w-xl font-body text-body-lg text-on-surface-variant">
          The manuscript has been fully grounded against your source material,
          imagery has been generated, and the print layout is finalized.
        </p>

        <div className="mb-8 grid grid-cols-2 gap-6 md:grid-cols-4">
          <Stat label="Length" value={String(artifact.page_count)} unit="pages" />
          <Stat
            label="File Size"
            value={artifact.file_size_mb.toFixed(1)}
            unit="MB"
          />
          <Stat label="Type" value="PDF" unit="Interior" />
          <Stat label="Format" value={trimLabel} />
        </div>

        {downloadError && (
          <p className="mb-4 font-body text-body-sm text-error">
            {downloadError}
          </p>
        )}

        <div className="flex flex-wrap gap-4">
          <Button
            onClick={handleDownload}
            disabled={downloading}
            className="px-8 py-3"
          >
            {downloading ? (
              <>
                <Spinner label="Downloading" />
                Downloading…
              </>
            ) : (
              <>
                <Icon name="download" />
                Download PDF
              </>
            )}
          </Button>
          <Button
            variant="outline"
            onClick={handlePreview}
            disabled={previewing}
            className="px-8 py-3"
          >
            <Icon name="visibility" />
            Online Preview
          </Button>
        </div>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  unit,
}: {
  label: string;
  value: string;
  unit?: string;
}) {
  return (
    <div>
      <p className="mb-1 font-label text-label-sm uppercase tracking-widest text-outline">
        {label}
      </p>
      <p className="font-display text-headline-sm text-on-surface">
        {value}{" "}
        {unit && (
          <span className="font-body text-body-sm text-secondary">{unit}</span>
        )}
      </p>
    </div>
  );
}
