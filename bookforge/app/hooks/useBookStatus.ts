"use client";

import { useCallback, useEffect, useState } from "react";
import { getBook, type BookStatus } from "@/lib/api";
import { usePolling } from "./usePolling";

export function useBookStatus(bookId: string) {
  const [book, setBook] = useState<BookStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getBook(bookId);
      setBook(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load status");
    } finally {
      setLoading(false);
    }
  }, [bookId]);

  const isActive = book?.status === "queued" || book?.status === "running";

  useEffect(() => {
    void fetchStatus();
  }, [fetchStatus]);

  usePolling(fetchStatus, {
    enabled: isActive,
    intervalMs: 2000,
    maxIntervalMs: 10000,
  });

  return { book, error, loading, isActive, refetch: fetchStatus };
}
