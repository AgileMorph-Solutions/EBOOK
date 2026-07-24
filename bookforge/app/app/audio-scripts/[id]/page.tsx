"use client";

import { useParams } from "next/navigation";
import { ScriptDetail } from "@/components/audio/ScriptDetail";
import { useAudioScriptStatus } from "@/hooks/useAudioScriptStatus";

export default function AudioScriptStatusPage() {
  const params = useParams();
  const scriptId = params.id as string;
  const { script, error, loading, isActive, refetch } =
    useAudioScriptStatus(scriptId);

  return (
    <ScriptDetail
      scriptId={scriptId}
      script={script}
      error={error}
      loading={loading}
      isActive={isActive}
      refetch={refetch}
    />
  );
}
