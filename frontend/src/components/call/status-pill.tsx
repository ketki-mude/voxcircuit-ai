import { cn } from "@/lib/utils";

export type MayaStatus = "Ready" | "Connecting" | "Listening" | "Speaking";

const STYLES: Record<MayaStatus, string> = {
  Ready: "border-border bg-muted text-muted-foreground",
  Connecting: "border-border bg-muted text-muted-foreground",
  Listening: "border-primary/30 bg-primary/10 text-primary",
  Speaking: "border-primary bg-primary text-primary-foreground",
};

export function StatusPill({ status }: { status: MayaStatus }) {
  const live = status === "Listening" || status === "Speaking";
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium",
        STYLES[status],
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full bg-current",
          live && "motion-safe:animate-pulse",
        )}
      />
      {status}
    </span>
  );
}
