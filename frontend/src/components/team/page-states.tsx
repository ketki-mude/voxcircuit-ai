import { RotateCw, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** A gray placeholder block shown while data loads. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn("block h-3 w-16 rounded bg-muted motion-safe:animate-pulse", className)}
    />
  );
}

/** Nothing to show yet, and what to do about it. */
export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-6 text-center">
      <p className="text-sm font-medium">{title}</p>
      {detail && <p className="text-sm text-muted-foreground">{detail}</p>}
      {action}
    </div>
  );
}

/** A failed load, with a way to try again. */
export function ErrorState({
  message,
  onRetry,
  compact = false,
}: {
  message: string;
  onRetry: () => void;
  /** One line, for use inside another element. */
  compact?: boolean;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-wrap items-center gap-2 text-sm",
        !compact && "rounded-lg border-[0.5px] border-destructive/40 bg-destructive/5 px-4 py-3",
      )}
    >
      <TriangleAlert aria-hidden className="size-4 shrink-0 text-destructive" />
      <span className="flex-1">{message}</span>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RotateCw />
        Retry
      </Button>
    </div>
  );
}
