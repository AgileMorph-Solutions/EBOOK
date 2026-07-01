"use client";

import { useEffect, useState } from "react";
import { checkHealth } from "@/lib/api";
import { JobCard } from "@/components/books/JobCard";
import { Alert } from "@/components/ui/Alert";
import { GlassPanel } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Skeleton } from "@/components/ui/Spinner";
import { useBooksList } from "@/hooks/useBooksList";
import { cn } from "@/lib/cn";

function PipelineStatusFooter({ doneCount }: { doneCount: number }) {
  const [healthy, setHealthy] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      const ok = await checkHealth();
      if (!cancelled) setHealthy(ok);
    }
    poll();
    const id = setInterval(poll, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="mt-4 flex flex-col gap-3 border-t border-outline-variant/20 pt-4">
      <div className="flex items-center gap-3 rounded-lg bg-tertiary/10 p-4">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            healthy === null && "animate-pulse bg-outline",
            healthy === true && "animate-pulse bg-tertiary",
            healthy === false && "bg-error"
          )}
        />
        <div>
          <p className="font-label text-label-md text-tertiary">
            Pipeline Status:{" "}
            {healthy === null
              ? "Checking…"
              : healthy
                ? "Operational"
                : "Offline"}
          </p>
          <p className="text-[10px] font-label text-label-sm text-on-surface-variant">
            {doneCount > 0
              ? `${doneCount} book${doneCount === 1 ? "" : "s"} completed`
              : "No completed books yet"}
          </p>
        </div>
      </div>
    </div>
  );
}

export function JobSidebar() {
  const { books, loading, error } = useBooksList();
  const doneCount = books.filter((b) => b.status === "done").length;

  return (
    <GlassPanel className="sticky top-28 flex h-[calc(100vh-160px)] flex-col rounded-2xl p-6">
      <div className="mb-6 flex items-center justify-between">
        <h2 className="font-display text-headline-sm text-on-surface">
          Recent Jobs
        </h2>
        <Icon name="history" className="text-secondary" />
      </div>

      <div
        id="jobs"
        className="custom-scrollbar flex-1 space-y-3 overflow-y-auto pr-2"
      >
        {loading && (
          <>
            {[1, 2, 3].map((i) => (
              <div key={i} className="rounded-xl p-4">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="mt-2 h-3 w-1/2" />
              </div>
            ))}
          </>
        )}

        {error && (
          <Alert variant="error">Could not reach API: {error}</Alert>
        )}

        {!loading && !error && books.length === 0 && (
          <p className="font-body text-body-sm text-secondary">
            No jobs yet. Create your first book using the form.
          </p>
        )}

        {!loading &&
          !error &&
          books.map((book) => <JobCard key={book.book_id} book={book} />)}
      </div>

      <PipelineStatusFooter doneCount={doneCount} />
    </GlassPanel>
  );
}

/** @deprecated Use JobSidebar */
export function JobList() {
  return <JobSidebar />;
}
