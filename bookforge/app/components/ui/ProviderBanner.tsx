"use client";

import { usePathname } from "next/navigation";
import { useHealth } from "@/hooks/useHealth";
import { useReadiness } from "@/hooks/useReadiness";
import { Icon } from "@/components/ui/Icon";

const BILLING_URL = "https://console.anthropic.com/settings/billing";

const BOOK_ONLY_REASONS = new Set([
  "quota_images",
  "layout",
  "capacity",
]);

function isAudioRoute(pathname: string | null): boolean {
  return pathname?.startsWith("/audio-scripts") ?? false;
}

/**
 * App-wide status banner. On book routes, shows readiness failures (credits,
 * image quota, layout). On audio routes, only LLM/provider issues — audio
 * scripts do not use Gemini images or the book layout pipeline.
 */
export function ProviderBanner() {
  const pathname = usePathname();
  const audioPage = isAudioRoute(pathname);
  const health = useHealth();
  const readiness = useReadiness();

  if (readiness && !readiness.can_generate) {
    if (
      audioPage &&
      readiness.reason_code &&
      BOOK_ONLY_REASONS.has(readiness.reason_code)
    ) {
      // Image quota / layout / book capacity do not block audio scripts.
    } else {
      const isCredits =
        readiness.reason_code === "quota_llm" || health?.unfunded === true;
      const title = audioPage
        ? isCredits
          ? "Can't start audio script right now"
          : "Audio script service unavailable"
        : readiness.title;
      const message = audioPage && isCredits
        ? "Writing credits are low. Add credits to generate transcripts."
        : readiness.message;
      return (
        <div
          role="alert"
          className="mb-6 flex items-start gap-3 rounded-xl border border-error/40 bg-error/15 px-4 py-3 text-error"
        >
          <Icon
            name={isCredits ? "account_balance_wallet" : "error"}
            size={20}
            className="mt-0.5 shrink-0"
          />
          <div className="flex-1">
            <p className="font-label text-label-md font-semibold">{title}</p>
            <p className="mt-0.5 font-body text-body-sm text-on-surface-variant">
              {message}
            </p>
            {isCredits && (
              <a
                href={BILLING_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-flex items-center gap-1 font-label text-label-sm font-semibold text-error underline underline-offset-2"
              >
                Recharge now
                <Icon name="open_in_new" size={14} />
              </a>
            )}
          </div>
        </div>
      );
    }
  }

  if (
    !audioPage &&
    readiness?.checks?.capacity &&
    !readiness.checks.capacity.ok
  ) {
    return (
      <div
        role="status"
        className="mb-6 flex items-start gap-3 rounded-xl border border-primary/30 bg-primary-fixed/20 px-4 py-3 text-on-primary-fixed-variant"
      >
        <Icon name="hourglass_top" size={20} className="mt-0.5 shrink-0" />
        <div>
          <p className="font-label text-label-md font-semibold">
            {readiness.checks.capacity.reason_code === "capacity"
              ? "Another book is generating"
              : "Generation queue"}
          </p>
          <p className="mt-0.5 font-body text-body-sm">
            Another book is already being generated. Yours will start when a
            slot opens.
          </p>
        </div>
      </div>
    );
  }

  if (!health) return null;

  if (health.unfunded) {
    return (
      <div
        role="alert"
        className="mb-6 flex items-start gap-3 rounded-xl border border-error/40 bg-error/15 px-4 py-3 text-error"
      >
        <Icon name="account_balance_wallet" size={20} className="mt-0.5 shrink-0" />
        <div className="flex-1">
          <p className="font-label text-label-md font-semibold">
            {audioPage
              ? "Writing credits are low — please recharge"
              : "AI credit balance is low — please recharge"}
          </p>
          <p className="mt-0.5 font-body text-body-sm text-on-surface-variant">
            {audioPage
              ? "Audio script generation is paused because the writing service is out of credits. Add credits to continue."
              : "Book generation is paused because the AI service is out of credits. Add credits to continue — any book you submit will start automatically once the balance is topped up (no need to resubmit)."}
          </p>
          <a
            href={BILLING_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 inline-flex items-center gap-1 font-label text-label-sm font-semibold text-error underline underline-offset-2"
          >
            Recharge now
            <Icon name="open_in_new" size={14} />
          </a>
        </div>
      </div>
    );
  }

  if (health.providerOk === false) {
    return (
      <div
        role="alert"
        className="mb-6 flex items-start gap-3 rounded-xl border border-primary/30 bg-primary-fixed/20 px-4 py-3 text-on-primary-fixed-variant"
      >
        <Icon name="cloud_off" size={20} className="mt-0.5 shrink-0" />
        <div>
          <p className="font-label text-label-md font-semibold">
            AI service is temporarily unavailable
          </p>
          <p className="mt-0.5 font-body text-body-sm">
            {health.detail ??
              (audioPage
                ? "We can't reach the writing service right now. Try again shortly."
                : "We can't reach the AI service right now. Submitted books will finish automatically once it's back.")}
          </p>
        </div>
      </div>
    );
  }

  return null;
}
