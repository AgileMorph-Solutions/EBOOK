const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type CreateBookRequest = {
  topic: string;
  text?: string;
  youtube_url?: string;
  target_pages?: number;
  chapter_limit?: number | null;
};

export type QAResult = {
  groundedness: number;
  unsupported_claims: unknown[];
  prose_quality_score: number;
  passed: boolean;
  layout_issues?: string[];
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
  status: "queued" | "running" | "done" | "failed";
  topic?: string;
  created_at?: string;
  target_pages?: number;
  chapter_limit?: number | null;
  source_type?: string;
  artifact?: BookArtifact;
  error?: string;
};

export type BookSummary = {
  book_id: string;
  topic: string;
  status: string;
  created_at: string;
  target_pages?: number;
  chapter_limit?: number | null;
};

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function downloadUrl(bookId: string): string {
  return `${API_URL}/books/${bookId}/download`;
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
