import { CircleCheck, CircleX, Info, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type Tone = "good" | "warning" | "critical" | "neutral";

const STYLES: Record<Tone, string> = {
  good: "border-success/40 bg-success/10 text-success",
  warning: "border-warning/40 bg-warning/10 text-warning",
  critical: "border-destructive/40 bg-destructive/10 text-destructive",
  neutral: "border-border bg-muted text-muted-foreground",
};

const ICONS = { good: CircleCheck, warning: TriangleAlert, critical: CircleX, neutral: Info };

/** A state label. Always an icon plus text, never color alone. */
export function StatusChip({ tone, children }: { tone: Tone; children: ReactNode }) {
  const Icon = ICONS[tone];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        STYLES[tone],
      )}
    >
      <Icon aria-hidden className="size-3" />
      {children}
    </span>
  );
}
