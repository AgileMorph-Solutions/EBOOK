"use client";

import { useEffect, useState } from "react";
import { getHealth, type HealthInfo } from "@/lib/api";

/** Polls /health so the UI can surface provider status (e.g. low AI balance). */
export function useHealth(intervalMs = 30_000) {
  const [health, setHealth] = useState<HealthInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const h = await getHealth();
      if (!cancelled) setHealth(h);
    };
    void poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return health;
}
