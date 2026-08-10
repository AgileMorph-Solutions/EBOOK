export type AppConfig = {
  apiUrl: string;
  trimWidthIn: number;
  trimHeightIn: number;
  contentModel: string;
  draftModel: string;
  imagePrimaryModel: string;
  imagePrimaryProvider: string;
};

let cachedConfig: AppConfig | null = null;

const DEFAULTS: AppConfig = {
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  trimWidthIn: 8.5,
  trimHeightIn: 11,
  contentModel: "claude-opus-5",
  draftModel: "claude-haiku-4-5-20251001",
  imagePrimaryModel: "imagen-4.0-generate-001",
  imagePrimaryProvider: "gemini",
};

export async function getAppConfig(): Promise<AppConfig> {
  if (typeof window === "undefined") return DEFAULTS;
  if (cachedConfig) return cachedConfig;

  try {
    const res = await fetch("/api/config");
    if (res.ok) {
      cachedConfig = (await res.json()) as AppConfig;
      return cachedConfig;
    }
  } catch {
    /* fall through */
  }
  return DEFAULTS;
}

export function formatTrimSize(width: number, height: number): string {
  const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
  return `${fmt(width)}" × ${fmt(height)}"`;
}

export function shortBookId(bookId: string): string {
  const parts = bookId.split("-");
  if (parts.length >= 2) {
    const suffix = parts[parts.length - 1];
    return `BF-${suffix.toUpperCase()}`;
  }
  return bookId.slice(0, 12);
}

export function formatBookDate(iso?: string): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-GB", {
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

export function formatDuration(startIso?: string, endIso?: string): string {
  if (!startIso) return "—";
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  const sec = Math.floor((end - start) / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  return `${min}m ${sec % 60}s`;
}
