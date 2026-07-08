"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input, Label, Textarea } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import {
  FileDropzone,
  type FileSelection,
} from "@/components/upload/FileDropzone";
import { SourceTabs, type SourceTab } from "@/components/upload/SourceTabs";
import {
  checkReadinessForRequest,
  createBook,
  createBookFromFile,
  getBook,
  getReadiness,
  type BookKind,
  type CreateBookRequest,
  type ReadinessResult,
} from "@/lib/api";

const YT_RE =
  /^(https?:\/\/)?(www\.)?(youtube\.com\/watch\?v=|youtu\.be\/)[\w-]{11}/;

export function CreateBookForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [tab, setTab] = useState<SourceTab>("paste");
  const [topic, setTopic] = useState("");
  const [description, setDescription] = useState("");
  const [toc, setToc] = useState("");
  const [text, setText] = useState("");
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [fileSelection, setFileSelection] = useState<FileSelection | null>(null);
  const [targetPages, setTargetPages] = useState(100);
  const [chapterLimit, setChapterLimit] = useState("");
  const [sectionLimit, setSectionLimit] = useState("");
  const [bookKind, setBookKind] = useState<BookKind>("practical_guide");
  const [submitting, setSubmitting] = useState(false);
  const [submitPhase, setSubmitPhase] = useState<
    "idle" | "uploading" | "starting"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const [serviceReadiness, setServiceReadiness] =
    useState<ReadinessResult | null>(null);
  const [requestReadiness, setRequestReadiness] =
    useState<ReadinessResult | null>(null);

  useEffect(() => {
    void getReadiness().then(setServiceReadiness);
  }, []);

  useEffect(() => {
    if (!topic.trim()) {
      setRequestReadiness(null);
      return;
    }
    const timer = setTimeout(() => {
      const tocLines = toc
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      const body: CreateBookRequest = {
        topic: topic.trim(),
        target_pages: targetPages,
        book_kind: bookKind,
        description: description.trim() || undefined,
        toc: tocLines.length > 0 ? tocLines : undefined,
      };
      if (tab === "youtube" && youtubeUrl.trim()) {
        body.youtube_url = youtubeUrl.trim();
      } else if (tab === "file" && fileSelection?.text) {
        body.text = fileSelection.text.trim();
      } else if (tab === "paste" && text.trim()) {
        body.text = text.trim();
      }
      void checkReadinessForRequest(body)
        .then(setRequestReadiness)
        .catch(() => setRequestReadiness(null));
    }, 500);
    return () => clearTimeout(timer);
  }, [
    topic,
    description,
    toc,
    targetPages,
    bookKind,
    tab,
    text,
    youtubeUrl,
    fileSelection,
  ]);

  useEffect(() => {
    const duplicateId = searchParams.get("duplicate");
    if (!duplicateId) return;
    getBook(duplicateId)
      .then((book) => {
        if (book.topic) setTopic(book.topic);
        if (book.book_kind) setBookKind(book.book_kind);
        if (book.target_pages) setTargetPages(Math.min(300, book.target_pages));
        if (book.chapter_limit != null) {
          setChapterLimit(String(book.chapter_limit));
        }
        if (book.section_limit != null) {
          setSectionLimit(String(book.section_limit));
        }
      })
      .catch(() => {
        /* ignore */
      });
  }, [searchParams]);

  const hasSource =
    tab === "youtube"
      ? youtubeUrl.trim().length > 0
      : tab === "file"
        ? fileSelection !== null
        : text.trim().length > 0;

  const readinessBlocked =
    serviceReadiness?.can_generate === false ||
    requestReadiness?.can_generate === false;
  const blockReadiness = requestReadiness?.can_generate === false
    ? requestReadiness
    : serviceReadiness?.can_generate === false
      ? serviceReadiness
      : null;

  const canSubmit =
    topic.trim().length > 0 && hasSource && !submitting && !readinessBlocked;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    if (tab === "youtube" && !YT_RE.test(youtubeUrl.trim())) {
      setError(
        "Enter a valid YouTube URL (youtube.com/watch?v=… or youtu.be/…)"
      );
      return;
    }

    setError(null);
    setSubmitting(true);

    try {
      const tocLines = toc
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);

      const common = {
        topic: topic.trim(),
        target_pages: targetPages,
        book_kind: bookKind,
        chapter_limit: chapterLimit ? parseInt(chapterLimit, 10) : null,
        section_limit: sectionLimit ? parseInt(sectionLimit, 10) : null,
        description: description.trim() || undefined,
        toc: tocLines.length > 0 ? tocLines : undefined,
      };

      let book_id: string;

      if (tab === "file" && fileSelection?.useMultipart) {
        setSubmitPhase("uploading");
        const formData = new FormData();
        formData.append("topic", common.topic);
        formData.append("file", fileSelection.file);
        formData.append("target_pages", String(common.target_pages));
        formData.append("book_kind", common.book_kind);
        if (common.chapter_limit != null) {
          formData.append("chapter_limit", String(common.chapter_limit));
        }
        if (common.section_limit != null) {
          formData.append("section_limit", String(common.section_limit));
        }
        if (common.description) {
          formData.append("description", common.description);
        }
        if (common.toc) {
          formData.append("toc", JSON.stringify(common.toc));
        }
        const res = await createBookFromFile(formData);
        book_id = res.book_id;
      } else {
        setSubmitPhase("starting");
        const body: CreateBookRequest = { ...common };
        if (tab === "youtube") {
          body.youtube_url = youtubeUrl.trim();
        } else if (tab === "file" && fileSelection) {
          body.text = fileSelection.text.trim();
        } else {
          body.text = text.trim();
        }
        const res = await createBook(body);
        book_id = res.book_id;
      }

      router.push(`/books/${book_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
      setSubmitting(false);
      setSubmitPhase("idle");
    }
  }

  const submitLabel =
    submitPhase === "uploading"
      ? "Uploading file…"
      : submitPhase === "starting"
        ? "Starting pipeline…"
        : "Generate ebook";

  return (
    <form onSubmit={handleSubmit} className="space-y-8">
      <div className="flex flex-col gap-2">
        <Label htmlFor="topic">
          Book topic <span className="text-primary">*</span>
        </Label>
        <Input
          id="topic"
          variant="underline"
          type="text"
          required
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="e.g. Forgotten British Meals Our Grandparents Lived On"
        />
        <p className="font-body text-body-sm italic text-secondary">
          This drives the title, chapter plan, art direction, and voice.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="description">
          Description{" "}
          <span className="font-normal text-secondary">(optional)</span>
        </Label>
        <Textarea
          id="description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={3}
          placeholder="Briefly describe the book's angle, audience, or key themes the AI should focus on…"
        />
      </div>

      <div className="flex flex-col gap-2">
        <Label htmlFor="toc">
          Table of contents{" "}
          <span className="font-normal text-secondary">(optional)</span>
        </Label>
        <Textarea
          id="toc"
          value={toc}
          onChange={(e) => setToc(e.target.value)}
          rows={6}
          placeholder={"One chapter or section title per line, e.g.:\nChapter 1: Introduction\nChapter 2: Core Concepts\nChapter 3: Advanced Techniques"}
        />
        <p className="font-body text-body-sm italic text-secondary">
          The book will follow your chapters exactly. Leave blank to let the AI plan the structure.
        </p>
      </div>

      <div className="space-y-4">
        <Label>
          Source material <span className="text-primary">*</span>
        </Label>
        <SourceTabs
          active={tab}
          onChange={(t) => {
            setTab(t);
            setError(null);
          }}
        />

        <div>
          {tab === "paste" && (
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={10}
              placeholder="Paste your source notes, transcript, or research here…"
            />
          )}
          {tab === "file" && (
            <FileDropzone onSelect={setFileSelection} disabled={submitting} />
          )}
          {tab === "youtube" && (
            <div>
              <Input
                type="url"
                value={youtubeUrl}
                onChange={(e) => setYoutubeUrl(e.target.value)}
                placeholder="https://www.youtube.com/watch?v=…"
              />
              {youtubeUrl && !YT_RE.test(youtubeUrl.trim()) && (
                <p className="mt-1 font-body text-body-sm text-primary">
                  Enter a valid YouTube watch or youtu.be URL
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor="pages">Target pages</Label>
          <Input
            id="pages"
            type="number"
            min={1}
            max={300}
            value={targetPages}
            onChange={(e) =>
              setTargetPages(Math.min(300, parseInt(e.target.value, 10) || 100))
            }
          />
          <p className="font-body text-body-sm italic text-secondary">
            Maximum 300 pages per book.
          </p>
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="book-kind">Book type</Label>
          <Select
            id="book-kind"
            value={bookKind}
            onChange={(e) => setBookKind(e.target.value as BookKind)}
          >
            <option value="practical_guide">Practical guide</option>
            <option value="narrative">Narrative</option>
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        {bookKind === "narrative" ? (
          <div className="flex flex-col gap-2">
            <Label htmlFor="chapters">
              Chapter limit{" "}
              <span className="font-normal text-secondary">(optional)</span>
            </Label>
            <Input
              id="chapters"
              type="number"
              min={1}
              placeholder="Full book"
              value={chapterLimit}
              onChange={(e) => setChapterLimit(e.target.value)}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <Label htmlFor="sections">
              Section limit{" "}
              <span className="font-normal text-secondary">(optional)</span>
            </Label>
            <Input
              id="sections"
              type="number"
              min={1}
              placeholder="Full guide"
              value={sectionLimit}
              onChange={(e) => setSectionLimit(e.target.value)}
            />
          </div>
        )}
      </div>

      {blockReadiness && (
        <Alert variant={blockReadiness.reason_code === "request" ? "warning" : "error"}>
          <p className="font-label text-label-md font-semibold">
            {blockReadiness.title}
          </p>
          <p className="mt-1 font-body text-body-sm">{blockReadiness.message}</p>
        </Alert>
      )}

      {error && <Alert variant="error">{error}</Alert>}

      <div className="pt-2">
        <Button
          type="submit"
          disabled={!canSubmit}
          className="w-full px-10 py-4 font-display text-headline-sm sm:w-auto"
        >
          {submitLabel}
          {submitPhase === "idle" && <Icon name="auto_awesome" />}
        </Button>
      </div>
    </form>
  );
}
