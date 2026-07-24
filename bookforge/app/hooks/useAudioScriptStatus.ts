"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getAudioScript, type AudioScriptStatus } from "@/lib/api";
import { usePolling } from "./usePolling";
import { useToast } from "@/components/ui/Toast";

export function useAudioScriptStatus(scriptId: string) {
  const [script, setScript] = useState<AudioScriptStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const { addToast } = useToast();
  const prevStatusRef = useRef<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getAudioScript(scriptId);

      if (
        prevStatusRef.current !== null &&
        prevStatusRef.current !== "failed" &&
        data.status === "failed"
      ) {
        addToast({
          variant: "error",
          title: "Script generation failed",
          message: data.error ?? "Something went wrong while writing the transcript.",
          duration: 8000,
        });
      }

      prevStatusRef.current = data.status;
      setScript(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load status");
    } finally {
      setLoading(false);
    }
  }, [scriptId, addToast]);

  const isActive =
    script?.status === "queued" || script?.status === "running";

  useEffect(() => {
    void fetchStatus();
  }, [fetchStatus]);

  usePolling(fetchStatus, {
    enabled: isActive,
    intervalMs: 2000,
    maxIntervalMs: 10000,
  });

  return { script, error, loading, isActive, refetch: fetchStatus };
}
