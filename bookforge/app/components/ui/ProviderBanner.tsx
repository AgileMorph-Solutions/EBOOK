"use client";

import { useHealth } from "@/hooks/useHealth";
import { Icon } from "@/components/ui/Icon";

const BILLING_URL = "https://console.anthropic.com/settings/billing";

/**
 * App-wide status banner. Shows a prominent "recharge" notice when the AI
 * credit balance is low, or a softer notice when the AI service is otherwise
 * unreachable. Renders nothing when everything is healthy.
 */
export function ProviderBanner() {
  const health = useHealth();
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
            AI credit balance is low — please recharge
          </p>
          <p className="mt-0.5 font-body text-body-sm text-on-surface-variant">
            Book generation is paused because the AI service is out of credits.
            Add credits to continue — any book you submit will start
            automatically once the balance is topped up (no need to resubmit).
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
              "We can't reach the AI service right now. Submitted books will finish automatically once it's back."}
          </p>
        </div>
      </div>
    );
  }

  return null;
}
