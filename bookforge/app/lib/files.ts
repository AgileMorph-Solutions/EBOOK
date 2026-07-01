import { ACCEPTED_FILE_EXTENSIONS, JSON_UPLOAD_THRESHOLD_BYTES } from "./constants";

export function isAcceptedFile(file: File): boolean {
  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  return ACCEPTED_FILE_EXTENSIONS.includes(ext);
}

export function shouldUseMultipartUpload(file: File): boolean {
  return file.size > JSON_UPLOAD_THRESHOLD_BYTES;
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
}

export function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
      } else {
        reject(new Error("Could not read file as text"));
      }
    };
    reader.onerror = () => reject(new Error("Failed to read file"));
    reader.readAsText(file);
  });
}

export function detectBinaryContent(text: string): boolean {
  // NUL bytes or high ratio of non-printable chars suggests binary
  if (text.includes("\0")) return true;
  const sample = text.slice(0, 4096);
  let nonPrintable = 0;
  for (let i = 0; i < sample.length; i++) {
    const code = sample.charCodeAt(i);
    if (code < 9 || (code > 13 && code < 32)) nonPrintable++;
  }
  return sample.length > 0 && nonPrintable / sample.length > 0.05;
}
