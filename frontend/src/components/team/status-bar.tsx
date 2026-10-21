import { CircleCheck, CircleX, LoaderCircle, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type StatusBarTone = "good" | "warning" | "critical" | "loading";

const STYLES: Record<StatusBarTone, string> = {
  good: "border-success/40 bg-success/10",
  warning: "border-warning/50 bg-warning/10",
  critical: "border-destructive/40 bg-destructive/5",
  loading: "border-line bg-card",
};

const ICONS: Record<StatusBarTone, ReactNode> = {
  good: <CircleCheck aria-hidden className="size-4 shrink-0 text-success" />,
  warning: <TriangleAlert aria-hidden className="size-4 shrink-0 text-warning" />,
  critical: <CircleX aria-hidden className="size-4 shrink-0 text-destructive" />,
  loading: (
    <LoaderCircle
      aria-hidden
      className="size-4 shrink-0 text-muted-foreground motion-safe:animate-spin"
    />
  ),
};

/** A slim, full-width line of status, optionally with rows underneath. */
export function StatusBar({
  tone,
  title,
  action,
  children,
}: {
  tone: StatusBarTone;
  title: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section
      aria-live="polite"
      className={cn("flex flex-col gap-3 rounded-lg border-[0.5px] px-4 py-3", STYLES[tone])}
    >
      <div className="flex flex-wrap items-center gap-2">
        {ICONS[tone]}
        <p className="flex-1 text-sm font-medium">{title}</p>
        {action}
      </div>
      {children}
    </section>
  );
}
