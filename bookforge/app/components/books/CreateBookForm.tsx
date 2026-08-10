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
  type FeasibilityOption,
  type ReadinessResult,
} from "@/lib/api";

function rewriteTopicCount(topic: string, newCount: number): string {
  return topic.replace(/\b\d{1,4}\b/, String(newCount));
}

function buildRequestBody(params: {
  topic: string;
  description: string;
  toc: string;
  text: string;
  targetPages: number;
  sectionLimit: string;
  densityMode: "standard" | "compact" | null;
  acceptedPromisedCount: number | null;
}): CreateBookRequest {
  // Drop bare-ordinal placeholder lines ("01", "02", ...) that come from
  // pasting a TOC formatted with the chapter number on its own line above
  // the title. The backend's TOC parser already merges these into the
  // following title (see _parse_toc_lines in bible.py); filtering them here
  // too keeps the live pre-submit readiness preview accurate, since it
  // reflects whatever array we send.
  const tocLines = params.toc
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && !/^\d{1,3}$/.test(l));
  return {
    topic: params.topic.trim(),
    target_pages: params.targetPages,
    book_kind: "practical_guide",
    section_limit: params.sectionLimit
      ? parseInt(params.sectionLimit, 10)
      : null,
    description: params.description.trim() || undefined,
    toc: tocLines.length > 0 ? tocLines : undefined,
    text: params.text.trim(),
    density_mode: params.densityMode,
    accepted_promised_count: params.acceptedPromisedCount,
  };
}

export function CreateBookForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [topic, setTopic] = useState("");
  const [description, setDescription] = useState("");
  const [toc, setToc] = useState("");
  const [text, setText] = useState("");
  const [targetPages, setTargetPages] = useState(100);
  const [sectionLimit, setSectionLimit] = useState("");
  const [densityMode, setDensityMode] = useState<"standard" | "compact" | null>(
    null
  );
  const [acceptedPromisedCount, setAcceptedPromisedCount] = useState<
    number | null
  >(null);
  const [submitting, setSubmitting] = useState(false);
  const [readinessLoading, setReadinessLoading] = useState(false);
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
      setReadinessLoading(false);
      return;
    }
    setReadinessLoading(true);
    const timer = setTimeout(() => {
      const body = buildRequestBody({
        topic,
        description,
        toc,
        text,
        targetPages,
        sectionLimit,
        densityMode,
        acceptedPromisedCount,
      });
      void checkReadinessForRequest(body)
        .then((res) => {
          setRequestReadiness(res);
          if (res.assessment?.assessment_mode === "compact") {
            setDensityMode("compact");
          } else if (
            res.feasibility?.status === "compact" &&
            res.assessment?.can_create
          ) {
            setDensityMode("compact");
          } else if (
            res.feasibility?.status === "ok" &&
            res.assessment?.assessment_mode === "standard"
          ) {
            setDensityMode(null);
          }
        })
        .catch(() => setRequestReadiness(null))
        .finally(() => setReadinessLoading(false));
    }, 500);
    return () => clearTimeout(timer);
  }, [
    topic,
    description,
    toc,
    targetPages,
    sectionLimit,
    text,
    densityMode,
    acceptedPromisedCount,
  ]);

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

  const readinessConfirmed =
    requestReadiness?.can_generate === true &&
    requestReadiness?.assessment?.can_create !== false;

  const readinessBlocked =
    serviceReadiness?.can_generate === false ||
    requestReadiness?.can_generate === false;

  const blockReadiness = requestReadiness?.can_generate === false
    ? requestReadiness
    : serviceReadiness?.can_generate === false
      ? serviceReadiness
      : null;

  const readyInfo =
    readinessConfirmed && requestReadiness ? requestReadiness : null;

  const canSubmit =
    topic.trim().length > 0 &&
    hasSource &&
    !submitting &&
    !readinessLoading &&
    readinessConfirmed;

  function handleFeasibilityOption(opt: FeasibilityOption) {
    if (opt.id === "raise_pages" && opt.target_pages) {
      setAcceptedPromisedCount(null);
      setTargetPages(Math.min(300, opt.target_pages));
      setDensityMode(null);
      return;
    }
    if (opt.id === "lower_pages" && opt.target_pages) {
      setTargetPages(Math.min(300, opt.target_pages));
      setAcceptedPromisedCount(null);
      setDensityMode(null);
      return;
    }
    if (opt.id === "lower_count" && opt.promised_count) {
      setAcceptedPromisedCount(opt.promised_count);
      setTopic((prev) => rewriteTopicCount(prev, opt.promised_count!));
      setDensityMode("compact");
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!topic.trim() || !hasSource || submitting) return;

    setError(null);
    setSubmitting(true);

    try {
      let body = buildRequestBody({
        topic,
        description,
        toc,
        text,
        targetPages,
        sectionLimit,
        densityMode,
        acceptedPromisedCount,
      });

      const fresh = await checkReadinessForRequest(body);
      setRequestReadiness(fresh);

      if (!fresh.can_generate || fresh.assessment?.can_create === false) {
        setSubmitting(false);
        return;
      }

      const resolvedDensity =
        fresh.assessment?.assessment_mode === "compact"
          ? "compact"
          : densityMode ??
            (fresh.feasibility?.status === "compact" ? "compact" : null);

      body = {
        ...body,
        density_mode: resolvedDensity,
      };

      const res = await createBook(body);
      router.push(`/books/${res.book_id}`);
    } catch (err) {
      // POST /books enqueues immediately after the coarse (free) readiness
      // check — it no longer runs real planning synchronously, so this only
      // catches "not ready" cases (quota/capacity/etc.), which come back as
      // a 503 with a ReadinessResult-shaped body (same contract as the
      // pre-check above). A genuine plan mismatch now surfaces as a failed
      // job on the book page instead. Route "not ready" through the same
      // alert + fix-it buttons instead of a flat error string.
      const detail = (err as (Error & { detail?: unknown }) | undefined)?.detail;
      if (detail && typeof detail === "object" && "can_generate" in detail) {
        setRequestReadiness(detail as ReadinessResult);
      } else {
        setError(err instanceof Error ? err.message : "Submission failed");
      }
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
          onChange={(e) => {
            setTopic(e.target.value);
            setAcceptedPromisedCount(null);
          }}
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
            onChange={(e) => {
              setTargetPages(Math.min(300, parseInt(e.target.value, 10) || 100));
              setAcceptedPromisedCount(null);
            }}
          />
          <p className="font-body text-body-sm italic text-secondary">
            Finished books stay within your target and up to ~20 extra pages
            (e.g. 100 → 100–120). Item counts in the title are kept unless you
            choose to reduce them.
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

      {readyInfo && (
        <RequestReadinessAlert readiness={readyInfo} />
      )}

      {blockReadiness &&
        (blockReadiness.reason_code === "request" ? (
          <RequestReadinessAlert
            readiness={blockReadiness}
            onChooseOption={handleFeasibilityOption}
          />
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

      {readinessLoading && topic.trim() && hasSource && (
        <Alert variant="info">
          <p className="font-body text-body-sm">Checking if this book can be generated…</p>
        </Alert>
      )}

      {error && <Alert variant="error">{error}</Alert>}

      <div className="pt-2">
        <Button
          type="submit"
          disabled={!canSubmit}
          className="w-full px-10 py-4 font-display text-headline-sm sm:w-auto"
        >
          {submitting
            ? "Starting…"
            : readinessLoading
              ? "Checking…"
              : "Generate ebook"}
          {!submitting && !readinessLoading && <Icon name="auto_awesome" />}
        </Button>
      </div>
    </form>
  );
}
