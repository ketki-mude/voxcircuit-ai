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

/** Use against a limit. The fill carries severity; the track is a lighter step of it. */
export function Meter({ label, value, max }: { label: string; value: number; max: number }) {
  const ratio = max > 0 ? Math.min(1, value / max) : 0;
  const severity = ratio >= 0.9 ? "critical" : ratio >= 0.7 ? "warning" : "normal";
  const fill = {
    normal: "bg-primary",
    warning: "bg-warning",
    critical: "bg-destructive",
  }[severity];
  const track = {
    normal: "bg-primary/15",
    warning: "bg-warning/20",
    critical: "bg-destructive/15",
  }[severity];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium">
          {value.toLocaleString()} of {max.toLocaleString()}
        </span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        className={cn("h-2 overflow-hidden rounded-full", track)}
      >
        <div className={cn("h-full rounded-full", fill)} style={{ width: `${ratio * 100}%` }} />
      </div>
    </div>
  );
}
