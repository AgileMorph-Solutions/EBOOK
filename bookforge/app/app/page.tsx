import { CreateBookForm } from "@/components/books/CreateBookForm";
import { JobSidebar } from "@/components/books/JobList";
import { GlassPanel } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Suspense } from "react";

function CreateFormFallback() {
  return (
    <div className="space-y-8">
      <div className="h-12 animate-pulse rounded-lg bg-surface-container-highest" />
      <div className="h-64 animate-pulse rounded-xl bg-surface-container-highest" />
    </div>
  );
}

const CAPABILITIES = [
  { icon: "verified", label: "Claim-grounded" },
  { icon: "auto_awesome", label: "Style-locked imagery" },
  { icon: "menu_book", label: "KDP print-ready" },
];

export default function HomePage() {
  return (
    <div className="grid grid-cols-1 gap-12 lg:grid-cols-12">
      <section className="animate-rise-in lg:col-span-8">
        <header className="mb-10 max-w-2xl">
          <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/[0.08] px-3 py-1 font-label text-label-sm uppercase tracking-widest text-primary">
            <span className="h-1.5 w-1.5 animate-ember-pulse rounded-full bg-primary" />
            AI Ebook Foundry
          </span>
          <h1 className="mb-4 font-display text-display-lg-mobile leading-[1.05] text-on-surface md:text-display-lg">
            Forge a{" "}
            <span className="text-ember-gradient">print-ready</span> ebook
          </h1>
          <p className="font-body text-body-lg leading-relaxed text-on-surface-variant">
            Provide a topic and source material — pasted text, a file, or a
            YouTube URL. BookForge grounds every claim, generates cinematic
            imagery, and delivers a KDP interior PDF.
          </p>

          <div className="mt-6 flex flex-wrap gap-2">
            {CAPABILITIES.map((c) => (
              <span
                key={c.label}
                className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-surface-container-low px-3 py-1.5 font-label text-label-sm text-on-surface-variant"
              >
                <Icon name={c.icon} size={14} className="text-primary" />
                {c.label}
              </span>
            ))}
          </div>
        </header>

        <GlassPanel className="rounded-2xl p-6 md:p-8">
          <Suspense fallback={<CreateFormFallback />}>
            <CreateBookForm />
          </Suspense>
        </GlassPanel>
      </section>

      <aside className="lg:col-span-4">
        <JobSidebar />
      </aside>
    </div>
  );
}
