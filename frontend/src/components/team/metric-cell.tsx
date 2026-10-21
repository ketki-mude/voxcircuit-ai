import type { ReactNode } from "react";

import { Skeleton } from "@/components/team/page-states";
import { cn } from "@/lib/utils";

export type CaptionTone = "neutral" | "good" | "warning" | "critical";

const CAPTION: Record<CaptionTone, string> = {
  neutral: "text-muted-foreground",
  good: "text-success",
  warning: "text-warning",
  critical: "text-destructive",
};

/**
 * One card divided into equal cells by hairlines. Holds MetricCells.
 * Each cell draws its own top and left border, and the grid is shifted up and left by 1px
 * so the outer card's border hides the first row's and column's. Borders, unlike 0.5px gaps,
 * never round away to nothing on standard-resolution screens.
 */
export function MetricStrip({
  label,
  className,
  children,
}: {
  label: string;
  /** Column classes, such as "md:grid-cols-4". */
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-lg border-[0.5px] border-line bg-card">
      <dl aria-label={label} className={cn("-mt-px -ml-px grid grid-cols-2", className)}>
        {children}
      </dl>
    </div>
  );
}

/** A label, a value and a one-line caption. A null value shows a loading placeholder. */
export function MetricCell({
  label,
  value,
  caption,
  tone = "neutral",
  title,
}: {
  label: string;
  value: string | null;
  caption?: ReactNode;
  /** Colors the caption only: the value stays in the text color. */
  tone?: CaptionTone;
  title?: string;
}) {
  return (
    <div
      className="flex flex-col gap-1 border-t-[0.5px] border-l-[0.5px] border-line p-4"
      title={title}
    >
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="text-2xl font-semibold">
        {value ?? <Skeleton className="my-1 h-6 w-12" />}
      </dd>
      <dd className={cn("min-h-4 text-xs", CAPTION[tone])}>
        {value === null ? <Skeleton className="w-20" /> : caption}
      </dd>
    </div>
  );
}
