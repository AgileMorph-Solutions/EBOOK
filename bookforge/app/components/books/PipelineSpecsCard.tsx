"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { formatDuration, getAppConfig, type AppConfig } from "@/lib/config";

type PipelineSpecsCardProps = {
  createdAt?: string;
  completedAt?: string;
};

export function PipelineSpecsCard({
  createdAt,
  completedAt,
}: PipelineSpecsCardProps) {
  const [config, setConfig] = useState<AppConfig | null>(null);

  useEffect(() => {
    void getAppConfig().then(setConfig);
  }, []);

  const generationTime = formatDuration(createdAt, completedAt);

  return (
    <Card variant="low" className="p-6">
      <h3 className="mb-4 font-label text-label-sm uppercase tracking-widest text-outline">
        Pipeline Specs
      </h3>
      <dl className="space-y-4 font-body text-body-sm">
        <SpecRow label="Model" value={config?.contentModel ?? "—"} />
        <SpecRow label="Draft Model" value={config?.draftModel ?? "—"} />
        <SpecRow label="Generation Time" value={generationTime} />
        <SpecRow
          label="Imagery Engine"
          value={
            config
              ? `${config.imagePrimaryProvider} / ${config.imagePrimaryModel}`
              : "—"
          }
        />
        <SpecRow label="Tokens In/Out" value="— / —" />
      </dl>
      <div className="mt-6 border-t border-outline/10 pt-6">
        <Button
          variant="ghost"
          disabled
          title="Coming soon"
          className="w-full justify-center py-2 font-label text-label-sm text-secondary"
        >
          <Icon name="terminal" size={16} />
          View Generation Logs
        </Button>
      </div>
    </Card>
  );
}

function SpecRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <dt className="text-secondary">{label}</dt>
      <dd className="font-label text-label-md text-on-surface">{value}</dd>
    </div>
  );
}
