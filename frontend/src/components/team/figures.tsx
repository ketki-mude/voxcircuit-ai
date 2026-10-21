import type { ReactNode } from "react";

import { DASH } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A labeled number. `value` is preformatted; null shows "--". */
export function StatTile({
  label,
  value,
  note,
}: {
  label: string;
  value: string | null;
  note?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border bg-card px-4 py-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="font-heading text-3xl font-semibold">{value ?? DASH}</dd>
      {note && <dd className="text-xs text-muted-foreground">{note}</dd>}
    </div>
  );
}

/**
 * Use against a limit, labeled "Label: N of MAX". The fill stays neutral until it needs
 * attention: amber from 80% of the limit, red once the limit is reached.
 */
export function Meter({ label, value, max }: { label: string; value: number; max: number }) {
  const ratio = max > 0 ? Math.min(1, value / max) : 0;
  const fill = ratio >= 1 ? "bg-destructive" : ratio >= 0.8 ? "bg-warning" : "bg-foreground/60";
  const text = `${label}: ${value.toLocaleString()} of ${max.toLocaleString()}`;
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm font-medium">{text}</p>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-valuetext={text}
        className="h-2 overflow-hidden rounded-full bg-muted"
      >
        <div className={cn("h-full rounded-full", fill)} style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}
