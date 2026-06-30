import { CreateBookForm } from "@/components/CreateBookForm";
import { JobList } from "@/components/JobList";

export default function HomePage() {
  return (
    <div className="grid gap-12 lg:grid-cols-[1fr_320px]">
      <section>
        <h1 className="font-serif text-3xl tracking-tight text-ink sm:text-4xl">
          Create a print-ready ebook
        </h1>
        <p className="mt-3 max-w-xl text-muted leading-relaxed">
          Provide a topic and source material — pasted text, a file, or a YouTube
          URL. BookForge grounds every claim, generates imagery, and delivers a KDP
          interior PDF.
        </p>
        <div className="mt-8 rounded-2xl border border-ink/10 bg-white/50 p-6 shadow-sm">
          <CreateBookForm />
        </div>
      </section>

      <aside>
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">
          Recent jobs
        </h2>
        <div className="mt-3">
          <JobList />
        </div>
      </aside>
    </div>
  );
}
