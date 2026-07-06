import type { Metadata } from "next";
import { EB_Garamond, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { PageShell } from "@/components/layout/Header";
import { AuthGate } from "@/components/layout/AuthGate";

const display = EB_Garamond({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["400", "500", "600", "700"],
});

const body = Inter({
  subsets: ["latin"],
  variable: "--font-body",
  weight: ["300", "400", "500", "600"],
});

const label = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-label",
  weight: ["400", "500"],
});

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
    <html lang="en" className="dark">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap"
          rel="stylesheet"
        />
      </head>
      <body
        className={`${display.variable} ${body.variable} ${label.variable} min-h-screen selection:bg-primary-fixed selection:text-on-primary-fixed`}
      >
        <AuthGate>
          <PageShell>{children}</PageShell>
        </AuthGate>
      </body>
    </html>
  );
}
