"use client";

import { SegmentedControl } from "@/components/ui/SegmentedControl";

export type SourceTab = "paste" | "file" | "youtube";

const TABS: { id: SourceTab; label: string }[] = [
  { id: "paste", label: "Paste text" },
  { id: "file", label: "Upload file" },
  { id: "youtube", label: "YouTube URL" },
];

type SourceTabsProps = {
  active: SourceTab;
  onChange: (tab: SourceTab) => void;
};

export function SourceTabs({ active, onChange }: SourceTabsProps) {
  return (
    <SegmentedControl
      options={TABS}
      value={active}
      onChange={onChange}
      aria-label="Source material type"
    />
  );
}
