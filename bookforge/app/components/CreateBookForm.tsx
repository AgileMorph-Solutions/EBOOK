"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { createBook } from "@/lib/api";

type SourceTab = "paste" | "file" | "youtube";

const YT_RE =
  /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/)[\w-]{11}/;

export function CreateBookForm() {
  const router = useRouter();
  const [tab, setTab] = useState<SourceTab>("paste");
  const [topic, setTopic] = useState("");
  const [text, setText] = useState("");
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [targetPages, setTargetPages] = useState(100);
  const [chapterLimit, setChapterLimit] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setFileName(file.name);
      const reader = new FileReader();
      reader.onload = () => {
        setText(typeof reader.result === "string" ? reader.result : "");
      };
      reader.readAsText(file);
    },
    []
  );

  const hasSource =
    tab === "youtube"
      ? youtubeUrl.trim().length > 0
      : text.trim().length > 0;

  const canSubmit = topic.trim().length > 0 && hasSource && !submitting;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    if (tab === "youtube" && !YT_RE.test(youtubeUrl.trim())) {
      setError("Enter a valid YouTube URL (youtube.com/watch?v=… or youtu.be/…)");
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      const body: Parameters<typeof createBook>[0] = {
        topic: topic.trim(),
        target_pages: targetPages,
        chapter_limit: chapterLimit ? parseInt(chapterLimit, 10) : null,
      };
      if (tab === "youtube") {
        body.youtube_url = youtubeUrl.trim();
      } else {
        body.text = text.trim();
      }
      const { book_id } = await createBook(body);
      router.push(`/books/${book_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
      setSubmitting(false);
    }
  }

  const tabs: { id: SourceTab; label: string }[] = [
    { id: "paste", label: "Paste text" },
    { id: "file", label: "Upload file" },
    { id: "youtube", label: "YouTube URL" },
  ];

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div>
        <label htmlFor="topic" className="block text-sm font-medium text-ink">
          Book topic <span className="text-accent">*</span>
        </label>
        <input
          id="topic"
          type="text"
          required
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="e.g. Forgotten British Meals Our Grandparents Lived On"
          className="mt-1.5 w-full rounded-lg border border-ink/15 bg-white px-4 py-2.5 text-ink shadow-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <p className="mt-1.5 text-xs text-muted">
          This drives the title, chapter plan, art direction, and voice.
        </p>
      </div>

      <div>
        <span className="block text-sm font-medium text-ink mb-2">
          Source material <span className="text-accent">*</span>
        </span>
        <div className="flex gap-1 rounded-lg border border-ink/10 bg-ink/[0.03] p-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setTab(t.id);
                setError(null);
              }}
              className={`flex-1 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                tab === t.id
                  ? "bg-white text-ink shadow-sm"
                  : "text-muted hover:text-ink"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="mt-3">
          {tab === "paste" && (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={10}
              placeholder="Paste your source notes, transcript, or research here…"
              className="w-full rounded-lg border border-ink/15 bg-white px-4 py-3 text-sm text-ink shadow-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent font-mono"
            />
          )}
          {tab === "file" && (
            <div className="rounded-lg border border-dashed border-ink/20 bg-white/80 p-6 text-center">
              <input
                type="file"
                accept=".txt,.md,.text"
                onChange={onFileChange}
                className="text-sm text-muted file:mr-4 file:rounded-md file:border-0 file:bg-accent file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-accent/90"
              />
              {fileName && (
                <p className="mt-2 text-sm text-moss">
                  Loaded: {fileName} ({text.length.toLocaleString()} chars)
                </p>
              )}
              <p className="mt-2 text-xs text-muted">.txt or .md files</p>
            </div>
          )}
          {tab === "youtube" && (
            <input
              type="url"
              value={youtubeUrl}
              onChange={(e) => setYoutubeUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=…"
              className="w-full rounded-lg border border-ink/15 bg-white px-4 py-2.5 text-ink shadow-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          )}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="pages" className="block text-sm font-medium text-ink">
            Target pages
          </label>
          <input
            id="pages"
            type="number"
            min={1}
            max={500}
            value={targetPages}
            onChange={(e) => setTargetPages(parseInt(e.target.value, 10) || 100)}
            className="mt-1.5 w-full rounded-lg border border-ink/15 bg-white px-4 py-2.5 shadow-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
        </div>
        <div>
          <label htmlFor="chapters" className="block text-sm font-medium text-ink">
            Chapter limit <span className="text-muted font-normal">(optional)</span>
          </label>
          <input
            id="chapters"
            type="number"
            min={1}
            placeholder="Full book"
            value={chapterLimit}
            onChange={(e) => setChapterLimit(e.target.value)}
            className="mt-1.5 w-full rounded-lg border border-ink/15 bg-white px-4 py-2.5 shadow-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
          />
          <p className="mt-1 text-xs text-muted">Leave empty for full chapter plan</p>
        </div>
      </div>

      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={!canSubmit}
        className="w-full rounded-lg bg-ink px-6 py-3 text-sm font-semibold text-paper shadow-md transition hover:bg-ink/90 disabled:cursor-not-allowed disabled:opacity-40 sm:w-auto"
      >
        {submitting ? "Starting pipeline…" : "Generate ebook"}
      </button>
    </form>
  );
}
