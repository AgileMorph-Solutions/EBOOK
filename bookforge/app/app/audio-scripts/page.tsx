import { CreateAudioScriptForm } from "@/components/audio/CreateAudioScriptForm";
import { ScriptSidebar } from "@/components/audio/ScriptSidebar";
import { GlassPanel } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";

const CAPABILITIES = [
  { icon: "psychology", label: "Retention-ordered" },
  { icon: "edit_note", label: "Word-banded items" },
  { icon: "description", label: "Speakable transcript" },
];

export default function AudioScriptsPage() {
  return (
    <div className="grid grid-cols-1 gap-12 lg:grid-cols-12">
      <section className="animate-rise-in lg:col-span-8">
        <header className="mb-10 max-w-2xl">
          <span className="mb-6 inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/[0.08] px-3 py-1 font-label text-label-sm uppercase tracking-widest text-primary">
            <span className="h-1.5 w-1.5 animate-ember-pulse rounded-full bg-primary" />
            Audio narration scripts
          </span>
          <h1 className="mb-4 font-display text-display-lg-mobile leading-[1.05] text-on-surface md:text-display-lg">
            Forge a{" "}
            <span className="text-ember-gradient">speakable</span> transcript
          </h1>
          <p className="font-body text-body-lg leading-relaxed text-on-surface-variant">
            Give a numbered title and category. BookForge brainstorms concepts,
            writes each item in the target word band, and returns a clean
            narration transcript.
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
          <CreateAudioScriptForm />
        </GlassPanel>
      </section>

      <aside className="lg:col-span-4">
        <ScriptSidebar />
      </aside>
    </div>
  );
}
