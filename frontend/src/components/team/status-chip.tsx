import { CircleCheck, CircleX, Info, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/** Status colors: green done, amber needs attention, red failed, gray for facts with no status. */
export type Tone = "good" | "warning" | "critical" | "neutral";

const STYLES: Record<Tone, string> = {
  good: "border-success/40 bg-success/10 text-success",
  warning: "border-warning/50 bg-warning/10 text-warning",
  critical: "border-destructive/40 bg-destructive/10 text-destructive",
  neutral: "border-line bg-muted text-muted-foreground",
};

const ICONS = { good: CircleCheck, warning: TriangleAlert, critical: CircleX, neutral: Info };

/** A state label. Always an icon plus text, never color alone. */
export function StatusChip({
  tone,
  icon,
  title,
  children,
}: {
  tone: Tone;
  /** Replaces the tone's default icon. */
  icon?: ReactNode;
  title?: string;
  children: ReactNode;
}) {
  const Icon = ICONS[tone];
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-full border-[0.5px] px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        STYLES[tone],
      )}
    >
      {icon ?? <Icon aria-hidden className="size-3" />}
      {children}
    </span>
  );
}
