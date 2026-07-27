let cachedApiUrl: string | null = null;

const BUILD_TIME_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function getApiUrl(): Promise<string> {
  if (typeof window === "undefined") {
    return process.env.API_URL ?? BUILD_TIME_URL;
  }
  if (cachedApiUrl) return cachedApiUrl;

  try {
    const res = await fetch("/api/config");
    if (res.ok) {
      const data = (await res.json()) as { apiUrl: string };
      cachedApiUrl = data.apiUrl;
      return cachedApiUrl;
    }
  } catch {
    /* fall through */
  }
  cachedApiUrl = BUILD_TIME_URL;
  return cachedApiUrl;
}

export function getApiUrlSync(): string {
  return cachedApiUrl ?? BUILD_TIME_URL;
}

export type BookKind = "practical_guide";

export type ErrorKind =
  | "quota_llm"
  | "quota_images"
  | "rate_limit"
  | "auth"
  | "network"
  | "unknown"
  | "layout"
  | "capacity"
  | "request"
  | "page_limit"
  | "empty_book"
  | "budget"
  | "timeout"
  | "source_unavailable";

export type CreateBookRequest = {
  topic: string;
  text?: string;
  youtube_url?: string;
  target_pages?: number;
  chapter_limit?: number | null;
  section_limit?: number | null;
  book_kind?: BookKind;
  description?: string;
  toc?: string[];
  density_mode?: "standard" | "compact" | null;
  accepted_promised_count?: number | null;
};

export type QAResult = {
  groundedness: number;
  unsupported_claims: unknown[];
  prose_quality_score: number;
  passed: boolean;
  layout_issues?: string[];
  safety_findings?: string[];
  image_issues?: string[];
};

export type BookArtifact = {
  book_id: string;
  interior_pdf_key?: string;
  page_count: number;
  file_size_mb: number;
  qa?: QAResult | null;
};

export type BookStatus = {
  book_id: string;
  status: "queued" | "running" | "waiting" | "done" | "failed";
  topic?: string;
  created_at?: string;
  target_pages?: number;
  chapter_limit?: number | null;
  section_limit?: number | null;
  book_kind?: BookKind;
  source_type?: string;
  source_filename?: string;
  stage?: string;
  progress?: number;
  eta_seconds?: number;
  elapsed_seconds?: number;
  stage_index?: number;
  stage_total?: number;
  artifact?: BookArtifact;
  error?: string;
  error_kind?: ErrorKind;
  error_type?: string;
  error_detail?: string;
  next_retry_at?: string;
  attempts?: number;
  wait_reason?: string;
  unfunded?: boolean;
  description?: string;
  toc?: string[];
  source_text?: string;
  youtube_url?: string;
};

export type BookSummary = {
  book_id: string;
  topic: string;
  status: string;
  created_at: string;
  target_pages?: number;
  chapter_limit?: number | null;
  book_kind?: BookKind;
};

import { formatApiError } from "./errors";

async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  skipJsonContentType = false
): Promise<T> {
  const apiUrl = await getApiUrl();
  const headers: Record<string, string> = {
  ...(init?.headers as Record<string, string> | undefined),
  };
  if (!skipJsonContentType) {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers,
  });

  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      /* ignore */
    }
    throw new Error(formatApiError(detail));
  }
  return res.json() as Promise<T>;
}

export type LoginResponse = { access_token: string; token_type: string };

export async function login(email: string, password: string): Promise<LoginResponse> {
  return apiFetch<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function fetchMe(): Promise<{ id: string; email: string; is_admin: boolean }> {
  return apiFetch("/auth/me");
}

export type HealthInfo = {
  ok: boolean;
  providerOk: boolean | null; // can we actually call the AI? null = no key
  unfunded: boolean; // AI credit balance too low
  canGenerate?: boolean;
  reasonCode?: ErrorKind | null;
  detail?: string;
};

export type CheckResult = {
  ok: boolean;
  reason_code?: ErrorKind | null;
  detail?: string;
};

export type FeasibilityOption = {
  id: "raise_pages" | "lower_count" | "lower_pages";
  label: string;
  target_pages?: number | null;
  promised_count?: number | null;
};

export type FeasibilityResult = {
  status: "ok" | "compact" | "infeasible";
  promised?: number | null;
  target_pages: number;
  page_range_lo?: number;
  page_range_hi?: number;
  pages_per_entry: number;
  min_pages_required: number;
  max_entries_at_target: number;
  estimated_pages?: number | null;
  message: string;
  options: FeasibilityOption[];
};

export type PlanScopeSummary = {
  sections: number;
  entries?: number | null;
  locked_toc: boolean;
};

export type BookAssessment = {
  can_create: boolean;
  target_pages: number;
  page_range_lo: number;
  page_range_hi: number;
  estimated_pages?: number | null;
  scope: PlanScopeSummary;
  status: "ok" | "compact" | "infeasible" | "needs_input";
  message: string;
  options: FeasibilityOption[];
  warnings: string[];
  assessment_mode?: "standard" | "compact" | "expand";
};

export type ReadinessResult = {
  can_generate: boolean;
  title: string;
  message: string;
  reason_code?: ErrorKind | null;
  checks: Record<string, CheckResult>;
  feasibility?: FeasibilityResult | null;
  assessment?: BookAssessment | null;
};

export async function getHealth(): Promise<HealthInfo> {
  try {
    const apiUrl = await getApiUrl();
    const res = await fetch(`${apiUrl}/health`, { cache: "no-store" });
    if (!res.ok) return { ok: false, providerOk: false, unfunded: false };
    const d = (await res.json()) as {
      ok?: boolean;
      provider_ok?: boolean | null;
      unfunded?: boolean;
      provider_detail?: string;
      can_generate?: boolean;
      reason_code?: ErrorKind | null;
    };
    return {
      ok: d.ok === true,
      providerOk: d.provider_ok ?? null,
      unfunded: d.unfunded === true,
      canGenerate: d.can_generate,
      reasonCode: d.reason_code ?? null,
      detail: d.provider_detail,
    };
  } catch {
    return { ok: false, providerOk: false, unfunded: false };
  }
}

export async function checkHealth(): Promise<boolean> {
  return (await getHealth()).ok;
}

export async function getReadiness(): Promise<ReadinessResult> {
  try {
    const apiUrl = await getApiUrl();
    const res = await fetch(`${apiUrl}/readiness`, { cache: "no-store" });
    if (!res.ok) {
      return {
        can_generate: false,
        title: "Can't check book service",
        message: "We couldn't verify whether generation is available. Try again shortly.",
        reason_code: "network",
        checks: {},
      };
    }
    return (await res.json()) as ReadinessResult;
  } catch {
    return {
      can_generate: false,
      title: "Can't check book service",
      message: "We couldn't verify whether generation is available. Try again shortly.",
      reason_code: "network",
      checks: {},
    };
  }
}

export async function checkReadinessForRequest(
  body: CreateBookRequest
): Promise<ReadinessResult> {
  return apiFetch<ReadinessResult>("/readiness", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function downloadUrl(bookId: string): string {
  return `${getApiUrlSync()}/books/${bookId}/download`;
}

export async function downloadBookPdf(
  bookId: string,
  filename: string
): Promise<void> {
  const apiUrl = await getApiUrl();
  const res = await fetch(`${apiUrl}/books/${bookId}/download`);
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      /* ignore */
    }
    throw new Error(formatApiError(detail));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function createBook(
  body: CreateBookRequest
): Promise<{ book_id: string; status: string }> {
  return apiFetch("/books", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getBook(bookId: string): Promise<BookStatus> {
  return apiFetch(`/books/${bookId}`);
}

export async function listBooks(): Promise<BookSummary[]> {
  const data = await apiFetch<{ books: BookSummary[] }>("/books");
  return data.books;
}

export async function deleteBook(bookId: string): Promise<void> {
  const apiUrl = await getApiUrl();
  const res = await fetch(`${apiUrl}/books/${bookId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(formatApiError(res.statusText));
  }
}

export type RegenerateResponse = {
  book_id: string;
  status: string;
  mode?: "rerender" | "full_rerun";
};

export async function regenerateBook(bookId: string): Promise<RegenerateResponse> {
  return apiFetch(`/books/${bookId}/regenerate`, { method: "POST" });
}

// --------------------------------------------------------------------------- #
// Audio scripts (transcript + optional ElevenLabs TTS)
// --------------------------------------------------------------------------- #

export type AudioScriptDurationEstimate = {
  title_duration_minutes: number | null;
  words_needed: number | null;
  overall_word_limit: number | null;
  estimated_minutes_from_cap: number | null;
  words_per_minute: number;
  conflict: boolean;
  reason: string;
};

export type CreateAudioScriptRequest = {
  title: string;
  /** Free-text niche for prompt voice (any category). */
  category?: string;
  item_limit?: number | null;
  word_count?: number | null;
  /** Total word budget across the whole script. */
  overall_word_limit?: number | null;
  /** Allow word cap below title duration when user confirms. */
  accept_short_duration?: boolean;
  /** When true, skip ElevenLabs and return transcript only. */
  skip_audio?: boolean;
};

export type AudioScriptSummary = {
  script_id: string;
  title: string;
  status: string;
  created_at: string;
  link?: string;
  has_audio?: boolean;
};

export type AudioScriptItem = {
  number: number;
  title: string;
  body: string;
  word_count: number;
};

export type AudioScriptStageMeta = {
  id: string;
  label: string;
  description: string;
};

export type AudioScriptStatus = {
  script_id: string;
  status: "queued" | "running" | "done" | "failed" | string;
  stage?: string;
  title?: string;
  category?: string;
  item_limit?: number | null;
  word_count?: number | null;
  overall_word_limit?: number | null;
  effective_word_count?: number | null;
  target_duration_minutes?: number | null;
  skip_audio?: boolean;
  created_at?: string;
  started_at?: string;
  item_count?: number;
  progress?: { done: number; total: number };
  stages?: AudioScriptStageMeta[];
  items?: AudioScriptItem[];
  transcript?: string;
  total_words?: number;
  audio_link?: string;
  audio_key?: string;
  audio_size_bytes?: number;
  error?: string;
  feedback?: string;
  link?: string;
};

export async function estimateAudioScript(body: {
  title: string;
  overall_word_limit?: number | null;
}): Promise<AudioScriptDurationEstimate> {
  return apiFetch("/audio-scripts/estimate", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function createAudioScript(
  body: CreateAudioScriptRequest
): Promise<{ script_id: string; status: string; link: string }> {
  return apiFetch("/audio-scripts", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type RegenerateAudioScriptResponse = {
  script_id: string;
  status: string;
  mode?: "full_rerun";
  link?: string;
};

export async function regenerateAudioScript(
  scriptId: string,
  body: { feedback: string }
): Promise<RegenerateAudioScriptResponse> {
  return apiFetch(`/audio-scripts/${scriptId}/regenerate`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function getAudioScript(
  scriptId: string
): Promise<AudioScriptStatus> {
  return apiFetch(`/audio-scripts/${scriptId}`);
}

export async function listAudioScripts(): Promise<AudioScriptSummary[]> {
  const data = await apiFetch<{ scripts: AudioScriptSummary[] }>(
    "/audio-scripts"
  );
  return data.scripts;
}

export function audioScriptUrl(scriptId: string): string {
  return `${getApiUrlSync()}/audio-scripts/${scriptId}/audio`;
}

export async function downloadAudioScriptMp3(
  scriptId: string,
  filename: string
): Promise<void> {
  const apiUrl = await getApiUrl();
  const res = await fetch(`${apiUrl}/audio-scripts/${scriptId}/audio`);
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      /* ignore */
    }
    throw new Error(formatApiError(detail));
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".mp3") ? filename : `${filename}.mp3`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
