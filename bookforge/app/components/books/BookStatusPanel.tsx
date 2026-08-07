"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { BookSuccessCard } from "@/components/books/BookSuccessCard";
import { PipelineProgress } from "@/components/books/PipelineProgress";
import { PipelineSpecsCard } from "@/components/books/PipelineSpecsCard";
import { PreviewGrid } from "@/components/books/PreviewGrid";
import { QualityAnalysisCard } from "@/components/books/QACard";
import { Alert } from "@/components/ui/Alert";
import { Breadcrumb } from "@/components/ui/Breadcrumb";
import { Button } from "@/components/ui/Button";
import { DetailsCollapsible } from "@/components/ui/DetailsCollapsible";
import { Input, Label, Textarea } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Spinner";
import { Icon } from "@/components/ui/Icon";
import { deleteBook, regenerateBook, type BookStatus, type ErrorKind } from "@/lib/api";
import { formatBookDate, shortBookId } from "@/lib/config";

function failureTitle(kind?: ErrorKind, unfunded?: boolean): string {
  switch (kind) {
    case "quota_llm":
      return "Writing credits exhausted";
    case "quota_images":
      return "Image allowance exhausted";
    case "rate_limit":
      return "Service temporarily busy";
    case "auth":
      return "Service configuration error";
    case "page_limit":
      return "Page count outside allowed range";
    case "empty_book":
      return "No chapters generated";
    case "request":
      return "Book settings need adjustment";
    case "layout":
      return "Layout failed";
    case "budget":
      return "Generation cost limit reached";
    case "timeout":
      return "Generation timed out";
    case "source_unavailable":
      return "Source material unavailable";
    default:
      return unfunded ? "AI credit balance is low" : "Generation failed";
  }
}

function formatElapsed(startIso?: string, elapsedSeconds?: number) {
  const sec =
    elapsedSeconds != null && Number.isFinite(elapsedSeconds)
      ? Math.max(0, Math.round(elapsedSeconds))
      : startIso
        ? Math.floor((Date.now() - new Date(startIso).getTime()) / 1000)
        : null;
  if (sec == null) return "";
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  return `${min}m ${sec % 60}s`;
}

type BookStatusPanelProps = {
  bookId: string;
  book: BookStatus | null;
  error: string | null;
  loading: boolean;
  isActive: boolean;
};

export function BookStatusPanel({
  bookId,
  book,
  error,
  loading,
  isActive,
}: BookStatusPanelProps) {
  const router = useRouter();
  const [archiving, setArchiving] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [regenerateError, setRegenerateError] = useState<string | null>(null);
  const [editingSettings, setEditingSettings] = useState(false);
  const [editTargetPages, setEditTargetPages] = useState<number | null>(null);
  const [editSourceText, setEditSourceText] = useState<string | null>(null);

  async function handleArchive() {
    if (archiving) return;
    if (!window.confirm("Archive this job? This permanently deletes the PDF and its record.")) {
      return;
    }
    setArchiving(true);
    setArchiveError(null);
    try {
      await deleteBook(bookId);
      router.push("/jobs");
    } catch (e) {
      setArchiveError(e instanceof Error ? e.message : "Archive failed");
      setArchiving(false);
    }
  }

  async function handleRegenerate(overrides?: { target_pages?: number; text?: string }) {
    if (regenerating) return;
    setRegenerating(true);
    setRegenerateError(null);
    try {
      await regenerateBook(bookId, overrides);
      setEditingSettings(false);
      router.refresh();
    } catch (e) {
      setRegenerateError(e instanceof Error ? e.message : "Regenerate failed");
    } finally {
      setRegenerating(false);
    }
  }

  function openEditSettings() {
    setEditTargetPages(book?.target_pages ?? 100);
    setEditSourceText(book?.source_text ?? "");
    setRegenerateError(null);
    setEditingSettings(true);
  }

  function handleRegenerateWithEdits() {
    const overrides: { target_pages?: number; text?: string } = {};
    if (editTargetPages != null && editTargetPages !== book?.target_pages) {
      overrides.target_pages = editTargetPages;
    }
    if (editSourceText != null && editSourceText.trim() && editSourceText !== book?.source_text) {
      overrides.text = editSourceText;
    }
    handleRegenerate(overrides);
  }

  if (loading && !book) {
    return (
      <div className="grid grid-cols-1 gap-gutter md:grid-cols-12">
        <div className="space-y-4 md:col-span-12">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-10 w-3/4" />
        </div>
        <div className="md:col-span-8">
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
        <div className="space-y-4 md:col-span-4">
          <Skeleton className="h-48 w-full rounded-xl" />
          <Skeleton className="h-40 w-full rounded-xl" />
        </div>
      </div>
    );
  }

  const subtitle = book?.created_at
    ? book.status === "done"
      ? `Generated on ${formatBookDate(book.created_at)}`
      : `Started ${formatBookDate(book.created_at)}`
    : undefined;

  return (
    <div className="grid grid-cols-1 gap-gutter md:grid-cols-12">
      <div className="mb-2 md:col-span-12">
        <Breadcrumb
          items={[
            { label: "Jobs", href: "/jobs" },
            { label: shortBookId(bookId) },
          ]}
        />
        <div className="mt-4">
          <PageHeader
            title={book?.topic ?? "Book job"}
            status={book?.status}
            subtitle={subtitle}
          />
        </div>
        {book?.source_filename && book.status !== "done" && (
          <p className="mt-2 font-label text-label-sm text-secondary">
            Source: {book.source_filename}
          </p>
        )}
      </div>

      {error && (
        <div className="md:col-span-12">
          <Alert variant="error">{error}</Alert>
        </div>
      )}

      {book && (book.status === "waiting" || (book.status === "running" && (book.error_kind === "quota_llm" || book.error_kind === "quota_images" || book.error_kind === "rate_limit" || book.unfunded))) && (
        <div className="md:col-span-12">
          <Alert variant={book.unfunded || book.error_kind === "quota_llm" ? "error" : "info"}>
            {(book.unfunded || book.error_kind === "quota_llm") && (
              <p className="font-medium">Out of writing credits</p>
            )}
            {book.error_kind === "quota_images" && (
              <p className="font-medium">Image allowance reached — auto-resuming when it resets</p>
            )}
            {book.error_kind === "rate_limit" && (
              <p className="font-medium">Writing service is busy — auto-resuming shortly</p>
            )}
            <p className="mt-1">
              {book.error ??
                "We're preparing your book — this is taking a little longer than usual. It will finish automatically, no action needed."}
            </p>
            {(book.unfunded || book.error_kind === "quota_llm") && (
              <a
                href="https://console.anthropic.com/settings/billing"
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block font-label text-label-sm font-semibold underline underline-offset-2"
              >
                Recharge credits →
              </a>
            )}
            {book.error_kind === "quota_images" && (
              <a
                href="https://ai.studio/billing"
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block font-label text-label-sm font-semibold underline underline-offset-2"
              >
                Manage billing →
              </a>
            )}
          </Alert>
        </div>
      )}

      {book && isActive && (
        <>
          <div className="md:col-span-8">
            <PipelineProgress
              status={book.status}
              stage={book.stage}
              bookKind={book.book_kind}
              elapsed={formatElapsed(book.created_at, book.elapsed_seconds)}
              progress={book.progress}
              etaSeconds={book.eta_seconds}
            />
          </div>
          <div className="md:col-span-4">
            <PipelineSpecsCard createdAt={book.created_at} />
            <div className="mt-4 rounded-xl bg-tertiary/10 p-4">
              <p className="font-label text-label-md text-tertiary">
                Pipeline Status: Active
              </p>
              <p className="mt-1 font-body text-body-sm text-secondary">
                Current stage: {book.stage ?? "starting"}
              </p>
            </div>
          </div>
        </>
      )}

      {book?.status === "failed" && (
        <div className="md:col-span-12">
          <Alert variant="error">
            <p className="font-medium">
              {failureTitle(book.error_kind, book.unfunded)}
            </p>
            <p className="mt-2 whitespace-pre-wrap font-body text-body-sm">
              {book.error}
            </p>
            {(book.error_kind === "quota_llm" || book.unfunded) && (
              <a
                href="https://console.anthropic.com/settings/billing"
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block font-label text-label-sm font-semibold underline underline-offset-2"
              >
                Recharge writing credits →
              </a>
            )}
            {book.error_kind === "quota_images" && (
              <>
                <p className="mt-2 font-label text-label-sm text-secondary">
                  Your book will continue automatically when the allowance resets — no need to resubmit.
                </p>
                <a
                  href="https://ai.studio/billing"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-block font-label text-label-sm font-semibold underline underline-offset-2"
                >
                  Manage billing →
                </a>
              </>
            )}
            {book.error_kind === "rate_limit" && (
              <p className="mt-2 font-label text-label-sm text-secondary">
                Wait a minute, then try submitting again.
              </p>
            )}
            {(book.error_type || book.error_detail || book.stage) && (
              <div className="mt-4">
                <DetailsCollapsible summary="Technical details — Show more">
                  <dl className="space-y-3 font-body text-body-sm">
                    {book.stage && (
                      <div>
                        <dt className="font-label text-label-sm text-secondary">
                          Stage
                        </dt>
                        <dd className="mt-1 font-mono text-[13px]">{book.stage}</dd>
                      </div>
                    )}
                    {book.error_type && (
                      <div>
                        <dt className="font-label text-label-sm text-secondary">
                          Error type
                        </dt>
                        <dd className="mt-1 font-mono text-[13px]">
                          {book.error_type}
                        </dd>
                      </div>
                    )}
                    {book.error_kind && (
                      <div>
                        <dt className="font-label text-label-sm text-secondary">
                          Error kind
                        </dt>
                        <dd className="mt-1 font-mono text-[13px]">
                          {book.error_kind}
                        </dd>
                      </div>
                    )}
                    {book.error_detail && (
                      <div>
                        <dt className="font-label text-label-sm text-secondary">
                          Detail
                        </dt>
                        <dd className="mt-1 whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed">
                          {book.error_detail}
                        </dd>
                      </div>
                    )}
                  </dl>
                </DetailsCollapsible>
              </div>
            )}
          </Alert>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Button
              variant="surface"
              type="button"
              disabled={regenerating}
              onClick={() => handleRegenerate()}
            >
              <Icon name={regenerating ? "hourglass_top" : "refresh"} />
              {regenerating ? "Regenerating…" : "Regenerate"}
            </Button>
            {!editingSettings && (
              <Button
                variant="outline"
                type="button"
                disabled={regenerating}
                onClick={openEditSettings}
              >
                <Icon name="tune" />
                Edit settings &amp; regenerate
              </Button>
            )}
            <Link href="/">
              <Button variant="outline" type="button">
                Create new book
              </Button>
            </Link>
          </div>
          {regenerateError && (
            <p className="mt-2 font-body text-body-sm text-error">
              {regenerateError}
            </p>
          )}
          <p className="mt-2 font-label text-label-sm text-secondary">
            Regenerate picks up from where it failed — if content and images
            were already generated, it re-renders for free; otherwise it
            re-runs the full pipeline with the same settings.
          </p>

          {editingSettings && (
            <div className="mt-4 rounded-xl border border-white/[0.08] bg-surface-container-low p-5">
              <p className="font-label text-label-md text-primary">
                Edit settings before regenerating
              </p>
              <p className="mt-1 font-body text-body-sm text-secondary">
                Changing either field wipes the previous plan and content for
                this book and starts a full re-run with the new settings —
                this is not free.
              </p>
              <div className="mt-4 flex flex-col gap-2">
                <Label htmlFor="edit-pages">Target pages</Label>
                <Input
                  id="edit-pages"
                  type="number"
                  min={1}
                  max={300}
                  value={editTargetPages ?? 100}
                  onChange={(e) =>
                    setEditTargetPages(
                      Math.min(300, parseInt(e.target.value, 10) || 100)
                    )
                  }
                />
              </div>
              <div className="mt-4 flex flex-col gap-2">
                <Label htmlFor="edit-source">Source material</Label>
                <Textarea
                  id="edit-source"
                  rows={8}
                  value={editSourceText ?? ""}
                  onChange={(e) => setEditSourceText(e.target.value)}
                  placeholder="Paste your source notes, transcript, or research here…"
                />
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <Button
                  variant="surface"
                  type="button"
                  disabled={regenerating}
                  onClick={handleRegenerateWithEdits}
                >
                  <Icon name={regenerating ? "hourglass_top" : "refresh"} />
                  {regenerating ? "Regenerating…" : "Regenerate with these settings"}
                </Button>
                <Button
                  variant="outline"
                  type="button"
                  disabled={regenerating}
                  onClick={() => setEditingSettings(false)}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {book?.status === "done" && book.artifact && (
        <>
          <div className="flex flex-col gap-8 md:col-span-8">
            <BookSuccessCard
              bookId={bookId}
              topic={book.topic ?? "ebook"}
              artifact={book.artifact}
            />
            <PreviewGrid
              topic={book.topic}
              description={book.description}
              toc={book.toc}
              targetPages={book.target_pages}
              sourceText={book.source_text}
              youtubeUrl={book.youtube_url}
              sourceFilename={book.source_filename}
              sourceType={book.source_type}
            />
          </div>

          <div className="flex flex-col gap-6 md:col-span-4">
            {book.artifact.qa && (
              <QualityAnalysisCard qa={book.artifact.qa} />
            )}
            <PipelineSpecsCard
              createdAt={book.created_at}
              completedAt={book.created_at}
            />

            <div className="flex flex-col gap-3">
              <Button
                variant="surface"
                className="w-full py-4"
                onClick={() =>
                  router.push(
                    `/?duplicate=${encodeURIComponent(bookId)}`
                  )
                }
              >
                <Icon name="content_copy" />
                Duplicate Project
              </Button>
              <Button
                variant="destructive"
                className="w-full py-4"
                disabled={archiving}
                onClick={handleArchive}
              >
                <Icon name={archiving ? "hourglass_top" : "delete"} />
                {archiving ? "Archiving…" : "Archive Job"}
              </Button>
              {archiveError && (
                <p className="text-center font-body text-body-sm text-error">
                  {archiveError}
                </p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
