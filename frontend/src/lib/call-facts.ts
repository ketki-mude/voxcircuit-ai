// Facts about a call, derived only from fields the control-room API returns.
// Nothing here invents values: when a field is missing, the result says so (null or "none").

import type { Tone } from "@/components/team/status-chip";
import type { CallDetail, CallRow } from "@/lib/team-api";

type Approval = CallDetail["approvals"][number];
type Handoff = CallDetail["handoffs"][number];

/** "switching_insurers" -> "Switching insurers". */
export function humanize(code: string): string {
  const words = code.replaceAll("_", " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The first 8 characters of a call's UUID, as a readable call number. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

// --- Calls list ---------------------------------------------------------------------------

export type ListOutcome = "booked" | "handed_off" | "not_booked" | "in_progress";

/** Outcome chips for a list row. A call can be both booked and handed off. */
export function listOutcomes(row: CallRow): ListOutcome[] {
  const outcomes: ListOutcome[] = [];
  if (row.booked) outcomes.push("booked");
  if (row.handoff_reason) outcomes.push("handed_off");
  if (outcomes.length === 0) outcomes.push(row.status === "active" ? "in_progress" : "not_booked");
  return outcomes;
}

export const OUTCOME_CHIP: Record<ListOutcome, { label: string; tone: Tone }> = {
  booked: { label: "Booked", tone: "good" },
  handed_off: { label: "Handed off", tone: "warning" },
  not_booked: { label: "Not booked", tone: "neutral" },
  in_progress: { label: "In progress", tone: "neutral" },
};

// --- Caller details -----------------------------------------------------------------------

export interface CallerDetails {
  fullName: string | null;
  email: string | null;
  reason: string | null;
  vehicle: string | null;
  zipCode: string | null;
  currentInsurer: string | null;
  renewalMonth: string | null;
}

/** What Maya recorded with create_or_update_lead, later calls overriding earlier ones. */
export function callerDetails(detail: CallDetail): CallerDetails | null {
  const recorded: Record<string, unknown> = {};
  for (const tool of detail.tool_calls) {
    if (tool.name === "create_or_update_lead" && tool.status === "ok") {
      for (const [key, value] of Object.entries(tool.args)) {
        if (value !== null && value !== undefined && value !== "") recorded[key] = value;
      }
    }
  }
  if (Object.keys(recorded).length === 0) return null;
  const text = (key: string) => (recorded[key] === undefined ? null : String(recorded[key]));
  const make = text("vehicle_make");
  const year = text("vehicle_year");
  return {
    fullName: text("full_name"),
    email: text("email"),
    reason: recorded.reason === undefined ? null : humanize(String(recorded.reason)),
    vehicle: make || year ? [year, make].filter(Boolean).join(" ") : null,
    zipCode: text("zip_code"),
    currentInsurer: text("current_insurer"),
    renewalMonth: text("renewal_month"),
  };
}

// --- Booking ------------------------------------------------------------------------------

export type BookingKind =
  | "booked"
  | "failed"
  | "booking"
  | "awaiting_approval"
  | "cancelled"
  | "expired"
  | "none";

export interface BookingState {
  kind: BookingKind;
  /** The ticket the state is about; null when no booking was requested. */
  approval: Approval | null;
  /** Tickets replaced by a newer one in the same call. */
  replaced: number;
}

function isReplaced(approval: Approval): boolean {
  // A newer request cancels a pending ticket on the system's behalf.
  return approval.status === "cancelled" && approval.decided_by === "system";
}

/** The booking that matters for this call: the latest approved ticket, else the latest one. */
export function bookingState(detail: CallDetail): BookingState {
  const replaced = detail.approvals.filter(isReplaced).length;
  const current = detail.approvals.filter((a) => !isReplaced(a));
  const approved = current.filter((a) => a.status === "approved");
  const approval = approved.at(-1) ?? current.at(-1) ?? null;
  if (approval === null) return { kind: "none", approval: null, replaced };
  let kind: BookingKind;
  if (approval.status === "approved") {
    kind = approval.outcome === null ? "booking" : approval.outcome.success ? "booked" : "failed";
  } else if (approval.status === "pending") {
    kind = "awaiting_approval";
  } else if (approval.status === "expired") {
    kind = "expired";
  } else {
    kind = "cancelled";
  }
  return { kind, approval, replaced };
}

export type DetailOutcome = "booked" | "handed_off" | "not_booked" | "in_progress";

/** The call's headline outcome: booked wins, then handed off, then not booked. */
export function detailOutcomes(detail: CallDetail): DetailOutcome[] {
  const outcomes: DetailOutcome[] = [];
  if (bookingState(detail).kind === "booked") outcomes.push("booked");
  if (detail.handoffs.length > 0) outcomes.push("handed_off");
  if (outcomes.length === 0) {
    outcomes.push(detail.call.status === "active" ? "in_progress" : "not_booked");
  }
  return outcomes;
}

export type StepName = "caller_approved" | "crm_upsert" | "calendar_event";

export interface StepFact {
  name: StepName;
  label: string;
  status: "done" | "failed" | "pending" | "not_reached" | "cancelled" | "expired";
  attempts: number | null;
  idempotencyKey: string | null;
  error: string | null;
  at: string | null;
}

const STEP_LABELS: Record<StepName, string> = {
  caller_approved: "Caller approved",
  crm_upsert: "CRM saved",
  calendar_event: "Calendar event created",
};

/** Caller approval, then each booking step, as recorded on the ticket. */
export function bookingSteps(approval: Approval): StepFact[] {
  const approvedByCaller = approval.status === "approved" && approval.decided_by === "caller";
  const decision: StepFact["status"] = approvedByCaller
    ? "done"
    : approval.status === "pending"
      ? "pending"
      : approval.status === "expired"
        ? "expired"
        : approval.status === "cancelled"
          ? "cancelled"
          : "not_reached";
  const facts: StepFact[] = [
    {
      name: "caller_approved",
      label: STEP_LABELS.caller_approved,
      status: decision,
      attempts: null,
      idempotencyKey: null,
      error: null,
      at: approval.status === "pending" ? null : approval.decided_at,
    },
  ];
  for (const name of ["crm_upsert", "calendar_event"] as const) {
    const step = approval.steps.find((s) => s.step === name);
    facts.push({
      name,
      label: STEP_LABELS[name],
      status:
        step === undefined
          ? "not_reached"
          : step.status === "done"
            ? "done"
            : step.status === "failed"
              ? "failed"
              : "pending",
      attempts: step?.attempts ?? null,
      idempotencyKey: step?.idempotency_key ?? null,
      error: step?.last_error ?? null,
      at: null,
    });
  }
  return facts;
}

export function latestHandoff(detail: CallDetail): Handoff | null {
  return detail.handoffs.at(-1) ?? null;
}

// --- What happened ------------------------------------------------------------------------

export interface TimelineEntry {
  key: string;
  at: string;
  title: string;
  detail?: string;
  tone: Tone;
}

const TOOL_TITLES: Record<string, string> = {
  create_or_update_lead: "Recorded the caller's details",
  check_availability: "Checked the calendar",
  request_booking: "Asked to book",
  start_verification: "Sent a verification code",
  get_previous_interactions: "Read the previous visit",
  handoff_to_human: "Asked for a person to take over",
};

const str = (value: unknown): string | null => (typeof value === "string" ? value : null);

/** The audit log as plain events, oldest first. Message events are left to the transcript. */
export function timeline(detail: CallDetail): TimelineEntry[] {
  const policyEvents = [...detail.policy_events];
  const entries: TimelineEntry[] = [];
  detail.audit.forEach((event, index) => {
    const p = event.payload;
    const base = { key: `${index}-${event.event}`, at: event.created_at };
    switch (event.event) {
      case "call.started":
        entries.push({ ...base, title: "Call started", tone: "neutral" });
        break;
      case "call.consent":
        entries.push({ ...base, title: "Caller accepted the notice", tone: "neutral" });
        break;
      case "call.rejected":
        entries.push({
          ...base,
          title: "Call refused",
          detail: str(p.reason) ? humanize(str(p.reason) as string) : undefined,
          tone: "critical",
        });
        break;
      case "call.ended":
        entries.push({
          ...base,
          title: "Call ended",
          detail: str(p.reason) ? humanize(str(p.reason) as string) : undefined,
          tone: "neutral",
        });
        break;
      case "tool.called": {
        const name = str(p.name) ?? "a tool";
        const ok = p.status === "ok";
        entries.push({
          ...base,
          title: TOOL_TITLES[name] ?? `Used ${name}`,
          detail: ok ? undefined : "The tool returned an error",
          tone: ok ? "neutral" : "critical",
        });
        break;
      }
      case "tool.cancelled":
        entries.push({ ...base, title: "A tool call was cancelled", tone: "neutral" });
        break;
      case "approval.created":
        entries.push({
          ...base,
          title: p.action === "reschedule" ? "Reschedule ticket shown" : "Booking ticket shown",
          tone: "neutral",
        });
        break;
      case "approval.replaced":
        entries.push({ ...base, title: "Ticket replaced by a newer one", tone: "neutral" });
        break;
      case "approval.decided": {
        const status = str(p.status);
        entries.push({
          ...base,
          title:
            status === "approved"
              ? "Caller approved the ticket"
              : status === "cancelled"
                ? "Caller cancelled the ticket"
                : status === "expired"
                  ? "Ticket expired"
                  : `Ticket ${status ?? "decided"}`,
          tone: status === "approved" ? "good" : "neutral",
        });
        break;
      }
      case "booking.started":
        entries.push({ ...base, title: "Booking started", tone: "neutral" });
        break;
      case "booking.completed": {
        const success = p.success === true;
        entries.push({
          ...base,
          title: success ? "Booking completed" : "Booking incomplete",
          tone: success ? "good" : "critical",
        });
        break;
      }
      case "booking.slot_taken":
        entries.push({ ...base, title: "The time was already taken", tone: "critical" });
        break;
      case "handoff.created":
        entries.push({
          ...base,
          title: "Handed off to a person",
          detail: detail.handoffs.find((h) => h.id === p.handoff_id)?.label,
          tone: "warning",
        });
        break;
      case "handoff.taken":
        entries.push({ ...base, title: "A team member took over", tone: "neutral" });
        break;
      case "policy.violation": {
        const flagged = policyEvents.shift();
        entries.push({
          ...base,
          title: `Policy flag: ${humanize(str(p.rule) ?? flagged?.rule ?? "rule")}`,
          detail: flagged ? `“${flagged.excerpt}”` : undefined,
          tone: "warning",
        });
        break;
      }
      case "system_notice.sent":
        entries.push({ ...base, title: "System notice sent to Maya", tone: "neutral" });
        break;
      case "verification.started":
        entries.push({ ...base, title: "Verification code sent", tone: "neutral" });
        break;
      case "verification.attempt": {
        const result = str(p.result);
        entries.push({
          ...base,
          title: result === "verified" ? "Caller verified" : "Verification code checked",
          detail: result && result !== "verified" ? humanize(result) : undefined,
          tone: result === "verified" ? "good" : "warning",
        });
        break;
      }
      case "verification.limit":
        entries.push({ ...base, title: "Verification limit reached", tone: "warning" });
        break;
      case "customer.deleted":
        entries.push({ ...base, title: "Caller deleted their data", tone: "neutral" });
        break;
      default:
        // message.user, message.assistant: shown in the transcript.
        break;
    }
  });
  return entries;
}

// --- From the audit log -------------------------------------------------------------------

export interface AuditCheck {
  label: string;
  ok: boolean;
}

/** Only the checks the audit log can answer for this call. */
export function auditChecks(detail: CallDetail): AuditCheck[] {
  const events = detail.audit;
  const indexOf = (predicate: (e: CallDetail["audit"][number]) => boolean) =>
    events.findIndex(predicate);
  const checks: AuditCheck[] = [];

  checks.push({
    label: "Consent to the AI notice was recorded",
    ok: indexOf((e) => e.event === "call.consent") !== -1,
  });

  const bookingStarts = events
    .map((e, i) => (e.event === "booking.started" ? i : -1))
    .filter((i) => i !== -1);
  if (bookingStarts.length > 0) {
    const approvedBefore = bookingStarts.every((start) =>
      events
        .slice(0, start)
        .some((e) => e.event === "approval.decided" && e.payload.status === "approved"),
    );
    checks.push({ label: "The caller approved before any booking write", ok: approvedBefore });
  }

  const completed = events.filter((e) => e.event === "booking.completed");
  if (completed.length > 0) {
    checks.push({
      label: "Both booking steps finished",
      ok: completed.at(-1)?.payload.success === true,
    });
  }

  const violations = events
    .map((e, i) => (e.event === "policy.violation" ? i : -1))
    .filter((i) => i !== -1);
  if (violations.length > 0) {
    const corrected = violations.every((i) =>
      events.slice(i + 1).some((e) => e.event === "system_notice.sent"),
    );
    checks.push({ label: "Every policy flag was followed by a correction", ok: corrected });
  }

  const historyRead = indexOf(
    (e) => e.event === "tool.called" && e.payload.name === "get_previous_interactions",
  );
  if (historyRead !== -1) {
    const verifiedBefore = events
      .slice(0, historyRead)
      .some((e) => e.event === "verification.attempt" && e.payload.result === "verified");
    checks.push({ label: "The caller was verified before history was read", ok: verifiedBefore });
  }

  return checks;
}

// --- Durations ----------------------------------------------------------------------------

/** Seconds between two timestamps, or null while the call is still going. */
export function callSeconds(startedAt: string, endedAt: string | null): number | null {
  if (!endedAt) return null;
  return Math.max(0, (new Date(endedAt).getTime() - new Date(startedAt).getTime()) / 1000);
}
