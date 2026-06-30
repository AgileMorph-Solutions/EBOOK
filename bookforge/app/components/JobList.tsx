"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listBooks, type BookSummary } from "@/lib/api";
import { StatusBadge } from "./StatusBadge";

function formatDate(iso: string) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function JobList() {
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listBooks()
      .then(setBooks)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <p className="text-sm text-muted animate-pulse">Loading recent jobs…</p>
    );
  }

  if (error) {
    return (
      <p className="text-sm text-red-700 rounded-lg border border-red-200 bg-red-50 p-3">
        Could not reach API: {error}
      </p>
    );
  }

  if (books.length === 0) {
    return (
      <p className="text-sm text-muted">No jobs yet. Create your first book.</p>
    );
  }

  return (
    <ul className="divide-y divide-ink/10 rounded-xl border border-ink/10 bg-white/60">
      {books.map((book) => (
        <li key={book.book_id}>
          <Link
            href={`/books/${book.book_id}`}
            className="flex flex-col gap-1 px-4 py-3 hover:bg-ink/[0.03] transition-colors sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="truncate font-medium text-ink">
                {book.topic || book.book_id}
              </p>
              <p className="text-xs text-muted">{formatDate(book.created_at)}</p>
            </div>
            <StatusBadge status={book.status} />
          </Link>
        </li>
      ))}
    </ul>
  );
}
