import type { Tone } from "@/components/team/status-chip";
import { formatExact, formatTimeSeconds } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface TimelineItem {
  key: string;
  /** ISO timestamp. */
  at: string;
  title: string;
  detail?: string;
  tone: Tone;
}

const DOT: Record<Tone, string> = {
  good: "bg-success",
  warning: "bg-warning",
  critical: "bg-destructive",
  neutral: "bg-muted-foreground/50",
};

/** Events in time order, joined by a hairline. The dot's color repeats what the title says. */
export function Timeline({ items, label }: { items: TimelineItem[]; label: string }) {
  return (
    <ol aria-label={label} className="flex flex-col">
      {items.map((item, index) => (
        <li key={item.key} className="relative flex gap-3 pb-3 last:pb-0">
          {index < items.length - 1 && (
            <span aria-hidden className="absolute top-4 bottom-0 left-[3.5px] w-[0.5px] bg-line" />
          )}
          <span aria-hidden className={cn("mt-1.5 size-2 shrink-0 rounded-full", DOT[item.tone])} />
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <p className="text-sm">{item.title}</p>
              <time
                dateTime={item.at}
                title={formatExact(item.at)}
                className="text-xs text-muted-foreground tabular-nums"
              >
                {formatTimeSeconds(item.at)}
              </time>
            </div>
            {item.detail && (
              <p className="text-xs break-words text-muted-foreground">{item.detail}</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
