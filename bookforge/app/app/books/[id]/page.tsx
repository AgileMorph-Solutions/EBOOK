"use client";

import { useParams } from "next/navigation";
import { BookStatusPanel } from "@/components/books/BookStatusPanel";
import { useBookStatus } from "@/hooks/useBookStatus";

export default function BookStatusPage() {
  const params = useParams();
  const bookId = params.id as string;
  const { book, error, loading, isActive } = useBookStatus(bookId);

  return (
    <BookStatusPanel
      bookId={bookId}
      book={book}
      error={error}
      loading={loading}
      isActive={isActive}
    />
  );
}
