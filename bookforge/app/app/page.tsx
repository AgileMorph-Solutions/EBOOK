import { CreateBookForm } from "@/components/books/CreateBookForm";
import { JobSidebar } from "@/components/books/JobList";
import { Suspense } from "react";

function CreateFormFallback() {
  return (
    <div className="space-y-8">
      <div className="h-12 animate-pulse rounded-lg bg-surface-container-highest" />
      <div className="h-64 animate-pulse rounded-xl bg-surface-container-highest" />
    </div>
  );
}

export default function HomePage() {
  return (
    <div className="grid grid-cols-1 gap-12 lg:grid-cols-12">
      <section className="lg:col-span-8">
        <header className="mb-10 max-w-2xl">
          <h1 className="mb-4 font-display text-display-lg-mobile text-on-surface md:text-display-lg">
            Create a print-ready ebook
          </h1>
          <p className="font-body text-body-lg leading-relaxed text-on-surface-variant">
            Provide a topic and source material — pasted text, a file, or a
            YouTube URL. BookForge grounds every claim, generates imagery, and
            delivers a KDP interior PDF.
          </p>
        </header>
        <Suspense fallback={<CreateFormFallback />}>
          <CreateBookForm />
        </Suspense>
      </section>

      <aside className="lg:col-span-4">
        <JobSidebar />
      </aside>
    </div>
  );
}
