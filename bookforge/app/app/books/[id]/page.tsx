"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { StatusBadge } from "@/components/StatusBadge";
import { downloadUrl, getBook, type BookStatus } from "@/lib/api";

function formatElapsed(startIso?: string) {
  if (!startIso) return "";
  const start = new Date(startIso).getTime();
  const sec = Math.floor((Date.now() - start) / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  return `${min}m ${sec % 60}s`;
}

export default function BookStatusPage() {
  const params = useParams();
  const bookId = params.id as string;
  const [book, setBook] = useState<BookStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, setTick] = useState(0);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getBook(bookId);
      setBook(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load status");
    }
  }, [bookId]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    if (!book || (book.status !== "queued" && book.status !== "running")) {
      return;
    }
    const poll = setInterval(fetchStatus, 3000);
    const clock = setInterval(() => setTick((t) => t + 1), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(clock);
    };
  }, [book, fetchStatus]);

  const isActive = book?.status === "queued" || book?.status === "running";

  return (
    <div className="max-w-2xl">
      <Link
        href="/"
        className="text-sm text-muted hover:text-accent transition-colors"
      >
        ← New book
      </Link>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <h1 className="font-serif text-2xl text-ink">
          {book?.topic ?? "Book job"}
        </h1>
        {book && <StatusBadge status={book.status} />}
      </div>
      <p className="mt-1 font-mono text-xs text-muted">{bookId}</p>

      {error && (
        <p className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      )}

      {book && isActive && (
        <div className="mt-8 rounded-xl border border-blue-200 bg-blue-50/50 p-6">
          <div className="flex items-center gap-3">
            <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
            <p className="font-medium text-blue-900">
              Pipeline {book.status === "queued" ? "queued" : "running"}…
            </p>
          </div>
          <p className="mt-2 text-sm text-blue-800/80">
            This can take several minutes. Elapsed:{" "}
            {formatElapsed(book.created_at)}
          </p>
          <p className="mt-1 text-xs text-blue-700/70">
            Ingest → Bible → Content → Images → QA → Layout → PDF
          </p>
        </div>
      )}

      {book?.status === "failed" && (
        <div className="mt-8 rounded-xl border border-red-200 bg-red-50 p-6">
          <p className="font-medium text-red-900">Generation failed</p>
          <pre className="mt-2 whitespace-pre-wrap text-sm text-red-800">
            {book.error}
          </pre>
        </div>
      )}

      {book?.status === "done" && book.artifact && (
        <div className="mt-8 space-y-6">
          <div className="rounded-xl border border-moss/30 bg-moss/5 p-6">
            <p className="font-medium text-moss">Your ebook is ready</p>
            <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-muted">Pages</dt>
                <dd className="text-lg font-semibold text-ink">
                  {book.artifact.page_count}
                </dd>
              </div>
              <div>
                <dt className="text-muted">File size</dt>
                <dd className="text-lg font-semibold text-ink">
                  {book.artifact.file_size_mb} MB
                </dd>
              </div>
            </dl>
            <a
              href={downloadUrl(bookId)}
              download
              className="mt-6 inline-flex items-center rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white shadow hover:bg-accent/90"
            >
              Download PDF
            </a>
          </div>

          {book.artifact.qa && (
            <div className="rounded-xl border border-ink/10 bg-white/60 p-6">
              <p className="text-sm font-semibold text-ink">Quality check</p>
              <dl className="mt-3 space-y-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-muted">Groundedness</dt>
                  <dd>
                    {(book.artifact.qa.groundedness * 100).toFixed(0)}%
                    {book.artifact.qa.passed ? (
                      <span className="ml-2 text-moss">passed</span>
                    ) : (
                      <span className="ml-2 text-amber-700">review needed</span>
                    )}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-muted">Prose score</dt>
                  <dd>{book.artifact.qa.prose_quality_score.toFixed(2)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-muted">Unsupported claims</dt>
                  <dd>
                    {book.artifact.qa.unsupported_claims?.length ?? 0}
                  </dd>
                </div>
              </dl>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
