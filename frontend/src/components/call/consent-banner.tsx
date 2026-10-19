"use client";

import { Info } from "lucide-react";

import { Checkbox } from "@/components/ui/checkbox";
import { CONSENT_NOTICE } from "@/lib/call-protocol";

interface ConsentBannerProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled: boolean;
}

export function ConsentBanner({ checked, onCheckedChange, disabled }: ConsentBannerProps) {
  return (
    <section
      aria-label="Consent notice"
      className="flex flex-col gap-3 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <p className="flex items-start gap-2 text-sm">
        <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
        <span>{CONSENT_NOTICE}</span>
      </p>
      <label className="flex shrink-0 items-center gap-2 text-sm font-medium">
        <Checkbox
          checked={checked}
          onCheckedChange={(value) => onCheckedChange(value)}
          disabled={disabled}
        />
        I understand
      </label>
    </section>
  );
}
