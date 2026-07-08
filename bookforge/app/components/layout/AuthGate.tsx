"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getToken } from "@/lib/auth";

const PUBLIC_PATHS = new Set(["/login"]);

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (PUBLIC_PATHS.has(pathname)) {
      setReady(true);
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/config");
        if (res.ok) {
          const data = (await res.json()) as { authEnabled?: boolean };
          if (!data.authEnabled) {
            if (!cancelled) setReady(true);
            return;
          }
        }
      } catch {
        /* fall through to token check */
      }
      if (cancelled) return;
      if (!getToken()) {
        router.replace("/login");
        return;
      }
      setReady(true);
    })();

    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (!ready) return null;
  return <>{children}</>;
}
