"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input, Label, Textarea } from "@/components/ui/Input";
import { RequestReadinessAlert } from "@/components/books/RequestReadinessAlert";
import {
  checkReadinessForRequest,
  createBook,
  getBook,
  getReadiness,
  type CreateBookRequest,
  type ReadinessResult,
} from "@/lib/api";

export function CreateBookForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [topic, setTopic] = useState("");
  const [description, setDescription] = useState("");
  const [toc, setToc] = useState("");
  const [text, setText] = useState("");
  const [targetPages, setTargetPages] = useState(100);
  const [sectionLimit, setSectionLimit] = useState("");
  const [submitting, setSubmitting] = useState(false);
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
        book_kind: "practical_guide",
        section_limit: sectionLimit ? parseInt(sectionLimit, 10) : null,
        description: description.trim() || undefined,
        toc: tocLines.length > 0 ? tocLines : undefined,
        text: text.trim() || undefined,
      };
      void checkReadinessForRequest(body)
        .then(setRequestReadiness)
        .catch(() => setRequestReadiness(null));
    }, 500);
    return () => clearTimeout(timer);
  }, [topic, description, toc, targetPages, sectionLimit, text]);

  useEffect(() => {
    const duplicateId = searchParams.get("duplicate");
    if (!duplicateId) return;
    getBook(duplicateId)
      .then((book) => {
        if (book.topic) setTopic(book.topic);
        if (book.target_pages) setTargetPages(Math.min(300, book.target_pages));
        if (book.section_limit != null) {
          setSectionLimit(String(book.section_limit));
        }
        if (book.description) setDescription(book.description);
        if (book.toc?.length) setToc(book.toc.join("\n"));
      })
      .catch(() => {
        /* ignore */
      });
  }, [searchParams]);

  const hasSource = text.trim().length > 0;

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

    setError(null);
    setSubmitting(true);

    try {
      const tocLines = toc
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);

      const body: CreateBookRequest = {
        topic: topic.trim(),
        target_pages: targetPages,
        book_kind: "practical_guide",
        section_limit: sectionLimit ? parseInt(sectionLimit, 10) : null,
        description: description.trim() || undefined,
        toc: tocLines.length > 0 ? tocLines : undefined,
        text: text.trim(),
      };

      const res = await createBook(body);
      router.push(`/books/${res.book_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
      setSubmitting(false);
    }
  }

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
          Required for a Contents page in the PDF. The book will follow your
          chapters exactly. Leave blank to let the AI plan the structure.
        </p>
      </div>

      <div className="space-y-4">
        <Label htmlFor="source">
          Source material <span className="text-primary">*</span>
        </Label>
        <Textarea
          id="source"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={10}
          placeholder="Paste your source notes, transcript, or research here…"
        />
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
            Content and images scale to fit at most your target (max 300).
            Never exceeds target.
          </p>
        </div>
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
      </div>

      {blockReadiness &&
        (blockReadiness.reason_code === "request" ? (
          <RequestReadinessAlert readiness={blockReadiness} />
        ) : (
          <Alert variant="error">
            <p className="font-label text-label-md font-semibold">
              {blockReadiness.title}
            </p>
            <p className="mt-1 font-body text-body-sm">
              {blockReadiness.message}
            </p>
          </Alert>
        ))}

      {error && <Alert variant="error">{error}</Alert>}

      <div className="pt-2">
        <Button
          type="submit"
          disabled={!canSubmit}
          className="w-full px-10 py-4 font-display text-headline-sm sm:w-auto"
        >
          {submitting ? "Starting pipeline…" : "Generate ebook"}
          {!submitting && <Icon name="auto_awesome" />}
        </Button>
      </div>
    </form>
  );
}
