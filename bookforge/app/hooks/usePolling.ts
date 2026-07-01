"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type UsePollingOptions = {
  enabled?: boolean;
  intervalMs?: number;
  maxIntervalMs?: number;
  backoffFactor?: number;
  pauseWhenHidden?: boolean;
};

export function usePolling(
  callback: () => void | Promise<void>,
  {
    enabled = true,
    intervalMs = 3000,
    maxIntervalMs = 10000,
    backoffFactor = 1.5,
    pauseWhenHidden = true,
  }: UsePollingOptions = {}
) {
  const callbackRef = useRef(callback);
  const intervalRef = useRef(intervalMs);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  const tick = useCallback(async () => {
    if (pauseWhenHidden && typeof document !== "undefined" && document.hidden) {
      return;
    }
    await callbackRef.current();
    intervalRef.current = Math.min(
      Math.round(intervalRef.current * backoffFactor),
      maxIntervalMs
    );
  }, [backoffFactor, maxIntervalMs, pauseWhenHidden]);

  const resetInterval = useCallback(() => {
    intervalRef.current = intervalMs;
  }, [intervalMs]);

  useEffect(() => {
    if (!enabled) {
      if (timerRef.current) clearTimeout(timerRef.current);
      return;
    }

    resetInterval();

    const schedule = () => {
      timerRef.current = setTimeout(async () => {
        await tick();
        if (enabled) schedule();
      }, intervalRef.current);
    };

    void callbackRef.current();
    schedule();

    const onVisibility = () => {
      if (!document.hidden && enabled) {
        resetInterval();
        void callbackRef.current();
      }
    };
    const onFocus = () => {
      if (enabled) {
        resetInterval();
        void callbackRef.current();
      }
    };

    if (pauseWhenHidden) {
      document.addEventListener("visibilitychange", onVisibility);
    }
    window.addEventListener("focus", onFocus);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onFocus);
    };
  }, [enabled, tick, resetInterval, pauseWhenHidden]);

  return { resetInterval };
}
