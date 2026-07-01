"use client";

import Link from "next/link";
import type { BookSummary } from "@/lib/api";
import { StatusBadge } from "@/components/ui/Badge";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

function bookKindLabel(kind?: string) {
  if (kind === "narrative") return "Narrative";
  if (kind === "practical_guide") return "Practical Guide";
  return "Guide";
}

function bookKindIcon(kind?: string) {
  if (kind === "narrative") return "history_edu";
  return "book";
}

function formatRelative(iso: string) {
  if (!iso) return "—";
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const min = Math.floor(diff / 60000);
    if (min < 1) return "just now";
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return new Date(iso).toLocaleDateString("en-GB");
  } catch {
    return iso;
  }
}

type JobCardProps = {
  book: BookSummary;
};

export function JobCard({ book }: JobCardProps) {
  const isRunning =
    book.status === "running" || book.status === "queued";

  return (
    <Link
      href={`/books/${book.book_id}`}
      className={cn(
        "group relative block cursor-pointer overflow-hidden rounded-xl p-4 transition-all",
        isRunning
          ? "border border-primary/20 bg-white editorial-shadow hover:bg-primary/5"
          : "bg-surface-container-low hover:bg-surface-container",
        !isRunning && "opacity-90"
      )}
    >
      {isRunning && (
        <div className="absolute bottom-0 left-0 top-0 w-1 bg-primary" />
      )}
      <div className="flex flex-col gap-1">
        <div className="flex items-start justify-between">
          <h3 className="truncate pr-4 font-body text-body-md font-semibold text-on-surface">
            {book.topic || book.book_id}
          </h3>
          <StatusBadge status={book.status} />
        </div>
        <div className="mt-2 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Icon
              name={bookKindIcon(book.book_kind)}
              className="text-tertiary"
              size={14}
            />
            <span className="font-body text-body-sm text-secondary">
              {bookKindLabel(book.book_kind)}
            </span>
          </div>
          <span className="font-label text-label-sm text-outline">
            {formatRelative(book.created_at)}
          </span>
        </div>
      </div>
    </Link>
  );
}
