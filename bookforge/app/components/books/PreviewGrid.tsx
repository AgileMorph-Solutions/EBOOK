"use client";

import { useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

type PreviewGridProps = {
  topic?: string;
  description?: string;
  toc?: string[];
  targetPages?: number;
  sourceText?: string;
  youtubeUrl?: string;
  sourceFilename?: string;
  sourceType?: string;
};

const DESCRIPTION_PREVIEW_CHARS = 220;
const TOPIC_PREVIEW_CHARS = 140;
const SOURCE_PREVIEW_CHARS = 220;
const TOC_PREVIEW_COUNT = 4;

export function PreviewGrid({
  topic,
  description,
  toc,
  targetPages,
  sourceText,
  youtubeUrl,
  sourceFilename,
  sourceType,
}: PreviewGridProps) {
  const title = topic?.trim() || "Untitled book";
  const trimmedDescription = description?.trim();
  const trimmedSource = sourceText?.trim();
  const trimmedYoutube = youtubeUrl?.trim();
  const hasToc = Boolean(toc?.length);
  const pageTarget =
    targetPages != null && Number.isFinite(targetPages) ? targetPages : null;

  const sourceHint = sourceFilename
    ? `From ${sourceFilename}`
    : sourceType === "youtube"
      ? "YouTube transcript"
      : "Pasted notes or research";

  const hasSource = Boolean(trimmedSource || trimmedYoutube);
  const sourcePreview = trimmedYoutube
    ? trimmedYoutube
    : trimmedSource
      ? truncateText(trimmedSource, SOURCE_PREVIEW_CHARS)
      : undefined;
  const sourceExpandable = Boolean(
    trimmedYoutube ||
      (trimmedSource && trimmedSource.length > SOURCE_PREVIEW_CHARS)
  );

  return (
    <section className="space-y-3" aria-label="Book inputs">
      <div className="flex items-center gap-2 px-1">
        <Icon name="edit_note" className="text-primary" size={20} />
        <h3 className="font-label text-label-sm uppercase tracking-widest text-outline">
          Your inputs
        </h3>
      </div>

      <div className="overflow-hidden rounded-xl border border-white/[0.06] bg-surface-container-high editorial-shadow divide-y divide-white/[0.06]">
        <InputRow
          icon="menu_book"
          label="Book topic"
          hint="Drives title, cover art, and voice"
          isEmpty={!topic?.trim()}
          emptyLabel="No topic provided"
          preview={truncateText(title, TOPIC_PREVIEW_CHARS)}
          canExpand={title.length > TOPIC_PREVIEW_CHARS}
          expandLabel="Show full topic"
          collapseLabel="Show less"
        >
          <p className="font-display text-headline-sm leading-snug text-on-surface">
            {title}
          </p>
        </InputRow>

        <InputRow
          icon="subject"
          label="Description"
          isEmpty={!trimmedDescription}
          emptyLabel="No description provided"
          preview={truncateText(trimmedDescription ?? "", DESCRIPTION_PREVIEW_CHARS)}
          canExpand={(trimmedDescription?.length ?? 0) > DESCRIPTION_PREVIEW_CHARS}
          expandLabel="Show full description"
          collapseLabel="Show less"
        >
          <p className="whitespace-pre-wrap font-body text-body-md leading-relaxed text-on-surface">
            {trimmedDescription}
          </p>
        </InputRow>

        <InputRow
          icon="article"
          label="Source material"
          hint={sourceHint}
          isEmpty={!hasSource}
          emptyLabel="Source material was not saved for this job"
          preview={sourcePreview}
          canExpand={sourceExpandable}
          expandLabel={
            trimmedYoutube
              ? "Show link details"
              : "Show full source material"
          }
          collapseLabel="Show less"
        >
          {trimmedYoutube ? (
            <a
              href={trimmedYoutube}
              target="_blank"
              rel="noopener noreferrer"
              className="break-all font-body text-body-sm text-primary underline underline-offset-2 hover:text-primary/80"
            >
              {trimmedYoutube}
            </a>
          ) : (
            <div className="max-h-80 overflow-y-auto rounded-lg border border-white/[0.04] bg-surface-container-lowest/80 p-4">
              <pre className="whitespace-pre-wrap font-body text-body-sm leading-relaxed text-on-surface">
                {trimmedSource}
              </pre>
            </div>
          )}
        </InputRow>

        <InputRow
          icon="list_alt"
          label="Table of contents"
          isEmpty={!hasToc}
          emptyLabel="AI-planned structure — no chapters were specified"
          preview={
            hasToc
              ? toc!
                  .slice(0, TOC_PREVIEW_COUNT)
                  .map((entry, i) => `${i + 1}. ${entry}`)
                  .join("\n")
              : undefined
          }
          canExpand={hasToc && toc!.length > TOC_PREVIEW_COUNT}
          expandLabel={`Show all ${toc?.length ?? 0} chapters`}
          collapseLabel="Show less"
        >
          <ol className="space-y-2.5">
            {(toc ?? []).map((entry, index) => (
              <li
                key={`${index}-${entry}`}
                className="flex gap-3 font-body text-body-sm text-on-surface"
              >
                <span className="mt-0.5 shrink-0 font-label text-label-sm tabular-nums text-primary">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="leading-relaxed">{entry}</span>
              </li>
            ))}
          </ol>
        </InputRow>

        <InputRow
          icon="auto_stories"
          label="Target pages"
          isEmpty={pageTarget == null}
          emptyLabel="Page target was not recorded"
          preview={
            pageTarget != null
              ? `${pageTarget} pages — finished books stay within target and up to ~20 extra pages`
              : undefined
          }
          canExpand={false}
        >
          <p className="font-display text-headline-md text-on-surface tabular-nums">
            {pageTarget}
            <span className="ml-2 font-body text-body-md text-secondary">pages</span>
          </p>
          <p className="mt-2 font-body text-body-sm text-secondary">
            Finished books stay within your target and up to ~20 extra pages.
          </p>
        </InputRow>
      </div>
    </section>
  );
}

function InputRow({
  icon,
  label,
  hint,
  isEmpty,
  emptyLabel,
  preview,
  canExpand,
  expandLabel = "Show more",
  collapseLabel = "Show less",
  children,
}: {
  icon: string;
  label: string;
  hint?: string;
  isEmpty: boolean;
  emptyLabel: string;
  preview?: string;
  canExpand: boolean;
  expandLabel?: string;
  collapseLabel?: string;
  children?: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="p-5">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10">
          <Icon name={icon} className="text-primary" size={18} />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <h4 className="font-label text-label-md text-on-surface">{label}</h4>
            {hint && (
              <span className="font-body text-body-sm text-secondary">{hint}</span>
            )}
          </div>

          {isEmpty ? (
            <p className="mt-2 font-body text-body-sm italic text-secondary">
              {emptyLabel}
            </p>
          ) : expanded ? (
            <div className="mt-3">{children}</div>
          ) : (
            <p
              className={cn(
                "mt-2 font-body text-body-sm leading-relaxed text-on-surface-variant",
                canExpand && "line-clamp-3"
              )}
            >
              {preview}
            </p>
          )}

          {!isEmpty && canExpand && (
            <button
              type="button"
              onClick={() => setExpanded((open) => !open)}
              className="mt-3 inline-flex items-center gap-1 font-label text-label-sm text-primary transition-colors hover:text-primary/80"
              aria-expanded={expanded}
            >
              {expanded ? collapseLabel : expandLabel}
              <Icon
                name="expand_more"
                size={18}
                className={cn(
                  "transition-transform",
                  expanded && "rotate-180"
                )}
              />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function truncateText(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  const slice = text.slice(0, maxChars);
  const lastSpace = slice.lastIndexOf(" ");
  const trimmed = lastSpace > maxChars * 0.6 ? slice.slice(0, lastSpace) : slice;
  return `${trimmed}…`;
}
