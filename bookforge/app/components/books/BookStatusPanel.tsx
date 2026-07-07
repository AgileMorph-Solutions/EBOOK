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
import { PageHeader } from "@/components/ui/PageHeader";
import { Skeleton } from "@/components/ui/Spinner";
import { Icon } from "@/components/ui/Icon";
import { deleteBook, retryBook, type BookStatus, type ErrorKind } from "@/lib/api";

const FAILURE_TITLES: Record<ErrorKind, string> = {
  quota_llm: "Out of LLM credits",
  quota_images: "Image generation quota exhausted",
  rate_limit: "Rate limited by upstream provider",
  auth: "Provider authentication failed",
  network: "Network error reaching an upstream service",
  unknown: "Generation failed",
};

function FailureAlert({
  kind,
  message,
  detail,
}: {
  kind?: ErrorKind;
  message?: string;
  detail?: string;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const k: ErrorKind = kind ?? "unknown";
  const title = FAILURE_TITLES[k];
  return (
    <Alert variant="error">
      <p className="font-medium">{title}</p>
      <p className="mt-2 font-body text-body-sm">
        {message ?? "Something went wrong during pipeline execution."}
      </p>
      {detail && detail !== message && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setShowDetail((v) => !v)}
            className="font-label text-label-sm text-secondary underline"
          >
            {showDetail ? "Hide technical details" : "Show technical details"}
          </button>
          {showDetail && (
            <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-surface-container-highest p-3 font-body text-body-sm text-secondary">
              {detail}
            </pre>
          )}
        </div>
      )}
    </Alert>
  );
}
import { formatBookDate, shortBookId } from "@/lib/config";

function formatElapsed(startIso?: string) {
  if (!startIso) return "";
  const start = new Date(startIso).getTime();
  const sec = Math.floor((Date.now() - start) / 1000);
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
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  async function handleRetry() {
    if (retrying) return;
    setRetrying(true);
    setRetryError(null);
    try {
      await retryBook(bookId);
      router.refresh();
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : "Retry failed");
      setRetrying(false);
    }
  }

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
        {book?.source_filename && (
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

      {book && isActive && (
        <>
          <div className="md:col-span-8">
            <PipelineProgress
              status={book.status}
              stage={book.stage}
              bookKind={book.book_kind}
              elapsed={formatElapsed(book.created_at)}
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
          <FailureAlert
            kind={book.error_kind}
            message={book.error}
            detail={book.error_detail}
          />
          {book.retry_count && book.retry_count > 0 ? (
            <p className="mt-3 font-label text-label-sm text-secondary">
              Previous attempts: {book.retry_count}
            </p>
          ) : null}
          {retryError && (
            <Alert variant="error" className="mt-4">
              {retryError}
            </Alert>
          )}
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={handleRetry}
              disabled={retrying}
            >
              {retrying ? "Retrying…" : "Retry pipeline"}
            </Button>
            <Link href="/">
              <Button variant="outline" type="button">
                Create new book
              </Button>
            </Link>
          </div>
          <p className="mt-2 font-label text-label-sm text-secondary">
            Retry reuses bible, chapters, and any generated images from the last attempt —
            only the failing stage and everything after it re-runs.
          </p>
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
            <PreviewGrid pageCount={book.artifact.page_count} />
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
