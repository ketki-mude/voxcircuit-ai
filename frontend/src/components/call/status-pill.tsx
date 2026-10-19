import { cn } from "@/lib/utils";

export type MayaStatus =
  | "Ready"
  | "Connecting"
  | "Listening"
  | "Speaking"
  | "Needs your OK"
  | "Booked"
  | "Not booked";

const STYLES: Record<MayaStatus, string> = {
  Ready: "border-border bg-muted text-muted-foreground",
  Connecting: "border-border bg-muted text-muted-foreground",
  Listening: "border-primary/30 bg-primary/10 text-primary",
  Speaking: "border-primary bg-primary text-primary-foreground",
  "Needs your OK": "border-warning/40 bg-warning/15 text-warning",
  Booked: "border-success/40 bg-success/15 text-success",
  "Not booked": "border-destructive/40 bg-destructive/10 text-destructive",
};

const LIVE: ReadonlySet<MayaStatus> = new Set(["Listening", "Speaking", "Needs your OK"]);

export function StatusPill({ status }: { status: MayaStatus }) {
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        STYLES[status],
      )}
    >
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full bg-current",
          LIVE.has(status) && "motion-safe:animate-pulse",
        )}
      />
      {status}
    </span>
  );
}
