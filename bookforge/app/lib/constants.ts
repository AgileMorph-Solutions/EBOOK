export const JSON_UPLOAD_THRESHOLD_BYTES = 512 * 1024; // 512 KB
export const ACCEPTED_FILE_EXTENSIONS = [".txt", ".md", ".text"];
export const ACCEPTED_FILE_MIME = "text/plain,text/markdown";

export const PIPELINE_STAGES = [
  { key: "ingest", label: "Ingest" },
  { key: "bible", label: "Bible" },
  { key: "content", label: "Content" },
  { key: "images", label: "Images" },
  { key: "qa", label: "QA" },
  { key: "layout", label: "Layout" },
] as const;

export type PipelineStageKey = (typeof PIPELINE_STAGES)[number]["key"];
