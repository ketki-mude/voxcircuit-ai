import { Check, Circle, LoaderCircle, Minus, X } from "lucide-react";

import type { Circuit, StepStatus } from "@/hooks/use-call";
import { cn } from "@/lib/utils";

const STEPS: { key: keyof Circuit; label: string }[] = [
  { key: "heard", label: "Heard" },
  { key: "understood", label: "Understood" },
  { key: "calendar", label: "Checked calendar" },
  { key: "approval", label: "Waiting for your OK" },
  { key: "booked", label: "Booked and saved to CRM" },
];

const STATUS_TEXT: Record<StepStatus, string> = {
  idle: "not started",
  active: "in progress",
  done: "done",
  failed: "failed",
  skipped: "stopped",
};

export function CircuitTimeline({
  circuit,
  lastAction,
}: {
  circuit: Circuit;
  lastAction: string | null;
}) {
  return (
    <div className="flex flex-col gap-3">
      <ol aria-label="What Maya is doing" className="grid gap-2 sm:grid-cols-5">
        {STEPS.map(({ key, label }) => {
          const status = circuit[key];
          return (
            <li
              key={key}
              className={cn(
                "flex items-center gap-2 rounded-lg border px-2.5 py-2 text-sm sm:flex-col sm:items-start sm:gap-1.5",
                status === "done" && "border-success/40 bg-success/10",
                status === "active" && "border-warning/50 bg-warning/10",
                status === "failed" && "border-destructive/40 bg-destructive/10",
                (status === "idle" || status === "skipped") && "text-muted-foreground",
              )}
            >
              <StepIcon status={status} />
              <span className="leading-snug">
                {label}
                <span className="sr-only">: {STATUS_TEXT[status]}</span>
              </span>
            </li>
          );
        })}
      </ol>
      {lastAction && (
        <p className="text-sm text-muted-foreground">
          <span className="font-medium text-foreground">Latest step:</span> {lastAction}
        </p>
      )}
    </div>
  );
}

function StepIcon({ status }: { status: StepStatus }) {
  const base = "size-4 shrink-0";
  switch (status) {
    case "done":
      return <Check aria-hidden className={cn(base, "text-success")} />;
    case "active":
      return <LoaderCircle aria-hidden className={cn(base, "text-warning motion-safe:animate-spin")} />;
    case "failed":
      return <X aria-hidden className={cn(base, "text-destructive")} />;
    case "skipped":
      return <Minus aria-hidden className={base} />;
    case "idle":
      return <Circle aria-hidden className={base} />;
  }
}
