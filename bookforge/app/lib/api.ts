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

export type BookKind = "practical_guide" | "narrative";

export type CreateBookRequest = {
  topic: string;
  text?: string;
  youtube_url?: string;
  target_pages?: number;
  chapter_limit?: number | null;
  section_limit?: number | null;
  book_kind?: BookKind;
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

export type ErrorKind =
  | "quota_llm"
  | "quota_images"
  | "rate_limit"
  | "auth"
  | "network"
  | "unknown";

export type BookStatus = {
  book_id: string;
  status: "queued" | "running" | "done" | "failed";
  topic?: string;
  created_at?: string;
  target_pages?: number;
  chapter_limit?: number | null;
  section_limit?: number | null;
  book_kind?: BookKind;
  source_type?: string;
  source_filename?: string;
  stage?: string;
  artifact?: BookArtifact;
  error?: string;
  error_kind?: ErrorKind;
  error_detail?: string;
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
import { clearAuth, getToken, redirectToLogin } from "./auth";

function withAuth(headers: Record<string, string>): Record<string, string> {
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

function handleUnauthorized(): void {
  clearAuth();
  redirectToLogin();
}

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
    headers: withAuth(headers),
  });

  if (res.status === 401) {
    handleUnauthorized();
  }

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

export async function checkHealth(): Promise<boolean> {
  try {
    const apiUrl = await getApiUrl();
    const res = await fetch(`${apiUrl}/health`, { cache: "no-store" });
    if (!res.ok) return false;
    const data = (await res.json()) as { ok?: boolean };
    return data.ok === true;
  } catch {
    return false;
  }
}

export function downloadUrl(bookId: string): string {
  return `${getApiUrlSync()}/books/${bookId}/download`;
}

export async function downloadBookPdf(
  bookId: string,
  filename: string
): Promise<void> {
  const apiUrl = await getApiUrl();
  const res = await fetch(`${apiUrl}/books/${bookId}/download`, {
    headers: withAuth({}),
  });
  if (res.status === 401) handleUnauthorized();
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

export async function createBookFromFile(
  formData: FormData
): Promise<{ book_id: string; status: string }> {
  return apiFetch(
    "/books/upload",
    {
      method: "POST",
      body: formData,
    },
    true
  );
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
  const res = await fetch(`${apiUrl}/books/${bookId}`, {
    method: "DELETE",
    headers: withAuth({}),
  });
  if (res.status === 401) handleUnauthorized();
  if (!res.ok && res.status !== 404) {
    throw new Error(formatApiError(res.statusText));
  }
}
