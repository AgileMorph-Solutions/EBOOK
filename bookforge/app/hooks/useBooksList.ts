"use client";

import { useCallback, useEffect, useState } from "react";
import { listBooks, type BookSummary } from "@/lib/api";
import { usePolling } from "./usePolling";

export function useBooksList() {
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchBooks = useCallback(async () => {
    try {
      const data = await listBooks();
      setBooks(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  const hasActive = books.some(
    (b) =>
      b.status === "queued" ||
      b.status === "running" ||
      b.status === "waiting"
  );

  useEffect(() => {
    void fetchBooks();
  }, [fetchBooks]);

  usePolling(fetchBooks, {
    enabled: hasActive,
    intervalMs: 5000,
    maxIntervalMs: 10000,
    backoffFactor: 1,
  });

  return { books, loading, error, refetch: fetchBooks };
}
