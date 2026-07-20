"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getBook, type BookStatus, type ErrorKind } from "@/lib/api";
import { usePolling } from "./usePolling";
import { useToast } from "@/components/ui/Toast";

const FAILURE_TITLES: Record<ErrorKind, string> = {
  quota_llm: "Out of AI writing credits",
  quota_images: "Image generation limit reached",
  rate_limit: "AI service is busy",
  auth: "Service configuration issue",
  network: "Connection error",
  layout: "Layout system error",
  capacity: "Pipeline at capacity",
  request: "Invalid book request",
  page_limit: "Page count outside allowed range",
  empty_book: "No chapters generated",
  budget: "Generation cost limit reached",
  timeout: "Generation timed out",
  source_unavailable: "Source material unavailable",
  unknown: "Generation failed",
};

export function useBookStatus(bookId: string) {
  const [book, setBook] = useState<BookStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const { addToast } = useToast();
  const prevStatusRef = useRef<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getBook(bookId);

      // Fire a toast exactly once when status transitions to "failed"
      if (
        prevStatusRef.current !== null &&
        prevStatusRef.current !== "failed" &&
        data.status === "failed"
      ) {
        const kind = data.error_kind ?? "unknown";
        addToast({
          variant: "error",
          title: FAILURE_TITLES[kind],
          message: data.error ?? "Something went wrong during pipeline execution.",
          duration: 8000,
        });
      }

      prevStatusRef.current = data.status;
      setBook(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load status");
    } finally {
      setLoading(false);
    }
  }, [bookId, addToast]);

  const isActive =
    book?.status === "queued" ||
    book?.status === "running" ||
    book?.status === "waiting";

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
