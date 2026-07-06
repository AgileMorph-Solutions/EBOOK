"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { fetchMe, login } from "@/lib/api";
import { setStoredUser, setToken } from "@/lib/auth";

function prettyLoginError(raw: string): string {
  const lower = raw.toLowerCase();
  if (lower.includes("invalid credentials")) {
    return "Incorrect email or password.";
  }
  if (lower.includes("missing bearer") || lower.includes("invalid token")) {
    return "Session expired. Please sign in again.";
  }
  if (lower.includes("valid email")) {
    return "Please enter a valid email address.";
  }
  if (lower.includes("failed to fetch") || lower.includes("networkerror")) {
    return "Cannot reach the API. Is the backend running?";
  }
  return raw;
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { access_token } = await login(email.trim(), password);
      setToken(access_token);
      const me = await fetchMe();
      setStoredUser(me);
      router.replace("/");
    } catch (err) {
      const raw = err instanceof Error ? err.message : "Login failed";
      setError(prettyLoginError(raw));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-[60vh] max-w-md flex-col justify-center">
      <h1 className="mb-6 font-display text-display-lg text-primary">Sign in</h1>
      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 font-label text-label-md text-secondary">
          Email
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded border border-outline-variant bg-surface-container-highest px-3 py-2 text-on-surface"
          />
        </label>
        <label className="flex flex-col gap-1 font-label text-label-md text-secondary">
          Password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded border border-outline-variant bg-surface-container-highest px-3 py-2 text-on-surface"
          />
        </label>
        {error && <p className="text-sm text-error">{error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-primary px-4 py-2 font-label text-label-md text-on-primary disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
