"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { JobCard } from "@/components/books/JobCard";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Skeleton } from "@/components/ui/Spinner";
import { useBooksList } from "@/hooks/useBooksList";

type Filter = "all" | "done" | "running" | "failed";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "done", label: "Done" },
  { id: "running", label: "Active" },
  { id: "failed", label: "Failed" },
];

export default function JobsPage() {
  const { books, loading, error } = useBooksList();
  const [filter, setFilter] = useState<Filter>("all");

  const counts = useMemo(
    () => ({
      all: books.length,
      done: books.filter((b) => b.status === "done").length,
      running: books.filter((b) => ["running", "queued", "waiting"].includes(b.status)).length,
      failed: books.filter((b) => b.status === "failed").length,
    }),
    [books]
  );

  const visible = books.filter((b) => {
    if (filter === "all") return true;
    if (filter === "running") return ["running", "queued", "waiting"].includes(b.status);
    return b.status === filter;
  });

  return (
    <div className="animate-rise-in mx-auto max-w-4xl">
      <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <span className="mb-3 inline-flex items-center gap-2 rounded-full border border-white/[0.06] bg-surface-container-low px-3 py-1 font-label text-label-sm uppercase tracking-widest text-secondary">
            <Icon name="history" size={14} className="text-primary" />
            History
          </span>
          <h1 className="font-display text-display-lg-mobile leading-[1.05] text-on-surface md:text-headline-md">
            Your ebook jobs
          </h1>
          <p className="mt-1 font-body text-body-md text-on-surface-variant">
            {counts.done} completed · {counts.running} active · {counts.failed} failed
          </p>
        </div>
        <Link href="/">
          <Button variant="primary">
            New book <Icon name="add" size={18} />
          </Button>
        </Link>
      </header>

      <div className="mb-6">
        <SegmentedControl
          options={FILTERS}
          value={filter}
          onChange={setFilter}
          aria-label="Filter jobs by status"
        />
      </div>

      {loading && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="rounded-xl border border-white/[0.05] p-4">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="mt-2 h-3 w-1/2" />
            </div>
          ))}
        </div>
      )}

      {error && <Alert variant="error">Could not reach API: {error}</Alert>}

      {!loading && !error && visible.length === 0 && (
        <div className="rounded-2xl border border-white/[0.06] bg-surface-container-low p-12 text-center">
          <Icon name="menu_book" size={40} className="text-outline" />
          <p className="mt-3 font-body text-body-md text-secondary">
            {books.length === 0
              ? "No jobs yet. Create your first book."
              : "No jobs match this filter."}
          </p>
        </div>
      )}

      {!loading && !error && visible.length > 0 && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {visible.map((book) => (
            <JobCard key={book.book_id} book={book} />
          ))}
        </div>
      )}
    </div>
  );
}
