import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BookForge",
  description: "Turn a topic and source into a print-ready ebook",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">
        <header className="border-b border-ink/10 bg-paper/80 backdrop-blur-sm sticky top-0 z-10">
          <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
            <a href="/" className="font-serif text-xl tracking-tight text-ink">
              Book<span className="text-accent">Forge</span>
            </a>
            <span className="text-sm text-muted">AI ebook pipeline</span>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
