import { Panel } from "@/components/team/panel";
import { StatusChip, type Tone } from "@/components/team/status-chip";
import {
  type BookingState,
  bookingState,
  bookingSteps,
  latestHandoff,
  type StepFact,
} from "@/lib/call-facts";
import { formatTimeSeconds, plural } from "@/lib/format";
import type { CallDetail } from "@/lib/team-api";

const STEP_CHIP: Record<StepFact["status"], { label: string; tone: Tone }> = {
  done: { label: "Done", tone: "good" },
  failed: { label: "Failed", tone: "critical" },
  pending: { label: "Waiting", tone: "neutral" },
  not_reached: { label: "Not reached", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  expired: { label: "Expired", tone: "neutral" },
};

/** One line saying what state the ticket ended in, from its status alone. */
const STATE_NOTE: Partial<Record<BookingState["kind"], string>> = {
  booking: "The caller approved. The booking steps are still running.",
  awaiting_approval: "The ticket is on the caller's screen, waiting for Approve or Cancel.",
  cancelled: "The caller cancelled the ticket.",
  expired: "The ticket expired before the caller approved it.",
};

/** The booking for one call: the appointment, each step's status, and any handoff. */
export function BookingCard({ detail }: { detail: CallDetail }) {
  const state = bookingState(detail);
  const handoff = latestHandoff(detail);
  const approval = state.approval;
  const reschedule = approval?.action === "reschedule";

  const chips = (
    <div className="flex flex-wrap gap-1">
      {state.kind === "booked" && (
        <StatusChip tone="good">{reschedule ? "Rescheduled" : "Booked"}</StatusChip>
      )}
      {handoff && <StatusChip tone="warning">Handed off</StatusChip>}
      {state.kind !== "booked" && !handoff && (
        <StatusChip tone="neutral">
          {detail.call.status === "active" && state.kind === "none" ? "In progress" : "Not booked"}
        </StatusChip>
      )}
      {state.kind === "failed" && <StatusChip tone="neutral">Needs follow-up</StatusChip>}
    </div>
  );

  return (
    <Panel title={reschedule ? "Reschedule" : "Booking"} action={chips}>
      {approval ? (
        <>
          <div className="flex flex-col gap-1">
            <p className="text-xs text-muted-foreground">
              {state.kind === "booked" ? "Appointment" : "Requested time"}
            </p>
            <p className="text-2xl font-semibold">{approval.outcome?.slot_label ?? approval.slot}</p>
          </div>

          <ol aria-label="Booking steps" className="flex flex-col divide-y-[0.5px] divide-line">
            {bookingSteps(approval).map((step) => (
              <li
                key={step.name}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm"
                title={step.idempotencyKey ? `Idempotency key ${step.idempotencyKey}` : undefined}
              >
                <span className="flex-1 font-medium">{step.label}</span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {step.at
                    ? formatTimeSeconds(step.at)
                    : step.attempts !== null
                      ? plural(step.attempts, "attempt", "attempts")
                      : ""}
                </span>
                <StatusChip tone={STEP_CHIP[step.status].tone}>
                  {STEP_CHIP[step.status].label}
                </StatusChip>
                {step.error && (
                  <p className="w-full text-xs break-words text-destructive">{step.error}</p>
                )}
              </li>
            ))}
          </ol>

          {approval.outcome ? (
            <p className="text-sm">{approval.outcome.summary}</p>
          ) : (
            STATE_NOTE[state.kind] && <p className="text-sm">{STATE_NOTE[state.kind]}</p>
          )}
        </>
      ) : (
        <p className="text-sm">
          {handoff ? "No appointment." : "No booking was requested in this call."}
        </p>
      )}

      {handoff && (
        <div className="flex flex-col gap-1 rounded-md border-[0.5px] border-warning/50 bg-warning/10 px-3 py-2">
          <p className="text-sm font-medium">Handed off: {handoff.label}</p>
          <p className="text-xs text-muted-foreground">
            {handoff.status === "taken"
              ? `Taken over${handoff.taken_by ? ` by ${handoff.taken_by}` : ""}${
                  handoff.taken_at ? ` at ${formatTimeSeconds(handoff.taken_at)}` : ""
                }.`
              : `Waiting for a person since ${formatTimeSeconds(handoff.created_at)}.`}
          </p>
        </div>
      )}

      {state.replaced > 0 && (
        <p className="text-xs text-muted-foreground">
          {plural(state.replaced, "earlier ticket was", "earlier tickets were")} replaced by a
          newer one.
        </p>
      )}
    </Panel>
  );
}
