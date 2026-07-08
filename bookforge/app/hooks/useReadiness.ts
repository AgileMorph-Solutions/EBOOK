"use client";

import { useEffect, useState } from "react";
import { getReadiness, type ReadinessResult } from "@/lib/api";

/** Polls /readiness so the create form can block before submit. */
export function useReadiness(intervalMs = 30_000) {
  const [readiness, setReadiness] = useState<ReadinessResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const r = await getReadiness();
      if (!cancelled) setReadiness(r);
    };
    void poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return readiness;
}
