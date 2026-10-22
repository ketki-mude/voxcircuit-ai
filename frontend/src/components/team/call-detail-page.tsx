"use client";

import { ArrowLeft, Check, X } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";

import { BookingCard } from "@/components/team/booking-card";
import { Disclosure } from "@/components/team/disclosure";
import { MetricCell, MetricStrip } from "@/components/team/metric-cell";
import { EmptyState, ErrorState, Skeleton } from "@/components/team/page-states";
import { Panel } from "@/components/team/panel";
import { StatusChip } from "@/components/team/status-chip";
import { Timeline } from "@/components/team/timeline";
import { useTeamResource, useTeamSession } from "@/hooks/use-team-session";
import {
  auditChecks,
  callerDetails,
  callSeconds,
  detailOutcomes,
  humanize,
  OUTCOME_CHIP,
  shortId,
  timeline,
} from "@/lib/call-facts";
import {
  formatDateTime,
  formatDuration,
  formatExact,
  formatMs,
  formatTimeSeconds,
  formatUsd,
  plural,
} from "@/lib/format";
import { type CallDetail, hasRole } from "@/lib/team-api";
import { cn } from "@/lib/utils";

/** The detail side of the Calls page, for the call in the URL. */
export function CallDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useTeamSession();
  const canOpen = hasRole(user, "agent");
  const detail = useTeamResource<CallDetail>(canOpen ? `/api/calls/${id}` : null, 5_000);

  return (
    <>
      <Link
        href="/control-room/calls"
        className="flex items-center gap-1 self-start rounded-md text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 lg:hidden"
      >
        <ArrowLeft aria-hidden className="size-4" />
        Latest calls
      </Link>
      {!canOpen ? (
        <AgentsOnly />
      ) : detail.data === null ? (
        detail.error === "Call not found" ? (
          <Panel>
            <EmptyState
              title="Call not found"
              detail="It may have been deleted when a caller deleted their data."
              action={
                <Link
                  href="/control-room/calls"
                  className="text-sm underline underline-offset-4 hover:text-foreground"
                >
                  Back to the latest calls
                </Link>
              }
            />
          </Panel>
        ) : detail.error ? (
          <ErrorState message={`Couldn't load this call. ${detail.error}`} onRetry={detail.reload} />
        ) : (
          <DetailSkeleton />
        )
      ) : (
        <CallView
          key={detail.data.call.id}
          data={detail.data}
          refreshError={detail.error}
          onRetry={detail.reload}
        />
      )}
    </>
  );
}

/** The detail pane for viewers: call details hold personal data, so they need the agent role. */
export function AgentsOnly() {
  return (
    <Panel title="Call details">
      <p className="text-sm">
        Opening a call shows the caller&rsquo;s details and transcript, so it needs the agent or
        admin role.
      </p>
      <p className="text-sm text-muted-foreground">Ask an admin if you need access.</p>
    </Panel>
  );
}

function CallView({
  data,
  refreshError,
  onRetry,
}: {
  data: CallDetail;
  refreshError: string | null;
  onRetry: () => void;
}) {
  const { call, usage } = data;
  const caller = callerDetails(data);
  const seconds = callSeconds(call.started_at, call.ended_at);
  const checks = auditChecks(data);
  const events = timeline(data);
  const flags = data.policy_events;
  const lastVerification = data.verification.at(-1);

  return (
    <>
      {refreshError && (
        <ErrorState compact message="Couldn't refresh this call." onRetry={onRetry} />
      )}

      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-2xl font-semibold">{caller?.fullName ?? "Name not recorded"}</h2>
          {detailOutcomes(data).map((outcome) => (
            <StatusChip key={outcome} tone={OUTCOME_CHIP[outcome].tone}>
              {OUTCOME_CHIP[outcome].label}
            </StatusChip>
          ))}
          {call.verified && <StatusChip tone="good">Verified caller</StatusChip>}
          {call.purpose === "evaluation" && <StatusChip tone="neutral">Evaluation</StatusChip>}
        </div>
        <p className="text-sm text-muted-foreground">
          Call{" "}
          <span className="font-mono" title={call.id}>
            {shortId(call.id)}
          </span>
          {" · "}
          <time dateTime={call.started_at} title={formatExact(call.started_at)}>
            {formatDateTime(call.started_at)}
          </time>
          {" · "}
          <span className="tabular-nums">
            {seconds === null ? "In progress" : formatDuration(seconds)}
          </span>
          {" · "}
          {call.input_mode === "audio" ? "Voice" : "Text"}, {call.engine} engine
        </p>
      </header>

      <BookingCard detail={data} />

      <div className="grid gap-6 xl:grid-cols-2 xl:items-start">
        <div className="flex flex-col gap-6">
          <Panel title="Caller details" footer="As Maya recorded them during the call.">
            {caller ? (
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
                <Detail label="Name" value={caller.fullName} />
                <Detail label="Email" value={caller.email} />
                <Detail label="Reason" value={caller.reason} />
                <Detail label="Vehicle" value={caller.vehicle} />
                <Detail label="ZIP code" value={caller.zipCode} />
                <Detail label="Current insurer" value={caller.currentInsurer} hideEmpty />
                <Detail label="Renewal month" value={caller.renewalMonth} hideEmpty />
                <Detail
                  label="Returning caller"
                  value={
                    call.verified
                      ? "Verified"
                      : lastVerification
                        ? `Not verified (${humanize(lastVerification.status).toLowerCase()})`
                        : "Not checked"
                  }
                />
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">Not recorded.</p>
            )}
          </Panel>

          <Panel title="From the audit log">
            <ul className="flex flex-col divide-y-[0.5px] divide-line">
              {checks.map((check) => (
                <li key={check.label} className="flex items-center gap-3 py-2 text-sm first:pt-0 last:pb-0">
                  {check.ok ? (
                    <Check aria-hidden className="size-4 shrink-0 text-success" />
                  ) : (
                    <X aria-hidden className="size-4 shrink-0 text-destructive" />
                  )}
                  <span className="flex-1">{check.label}</span>
                  <StatusChip tone={check.ok ? "good" : "critical"}>
                    {check.ok ? "Yes" : "No"}
                  </StatusChip>
                </li>
              ))}
            </ul>
          </Panel>
        </div>

        <Panel title="What happened">
          {events.length > 0 ? (
            <Timeline items={events} label="What happened" />
          ) : (
            <p className="text-sm text-muted-foreground">No events recorded.</p>
          )}
        </Panel>
      </div>

      <MetricStrip label="Call metrics" className="md:grid-cols-4">
        <MetricCell
          label="Latency p50"
          value={usage.latency_p50_ms === null ? "Not recorded" : formatMs(usage.latency_p50_ms)}
          caption={
            usage.latency_p95_ms === null
              ? `from ${plural(usage.latency_samples, "reply", "replies")}`
              : `p95 ${formatMs(usage.latency_p95_ms)}`
          }
        />
        <MetricCell
          label="Audio in / out"
          value={`${usage.audio_seconds_in.toFixed(1)} / ${usage.audio_seconds_out.toFixed(1)} s`}
          caption={call.input_mode === "audio" ? "caller / Maya" : "text call"}
        />
        <MetricCell
          label="Estimated cost"
          value={formatUsd(usage.estimated_cost_usd)}
          caption="at paid-tier audio rates"
        />
        <MetricCell
          label="Policy flags"
          value={flags.length.toLocaleString()}
          caption={
            flags.length === 0
              ? "none"
              : [...new Set(flags.map((flag) => humanize(flag.rule)))].join(", ")
          }
          tone={flags.length > 0 ? "warning" : "neutral"}
        />
      </MetricStrip>

      <Disclosure title="Transcript" meta={plural(data.messages.length, "message", "messages")}>
        {data.messages.length === 0 ? (
          <p className="text-sm text-muted-foreground">No messages stored.</p>
        ) : (
          <ol aria-label="Transcript" className="flex flex-col gap-2">
            {data.messages.map((message) => (
              <li
                key={message.seq}
                className={cn(
                  "flex flex-col gap-1 rounded-lg px-3 py-2",
                  message.role === "user" ? "bg-accent" : "bg-muted",
                )}
              >
                <span className="text-xs text-muted-foreground">
                  {message.role === "user" ? "Caller" : message.role === "assistant" ? "Maya" : "System"}
                  {" · "}
                  {formatTimeSeconds(message.created_at)}
                  {message.source === "typed" ? " · typed" : ""}
                  {message.latency_ms !== null ? ` · ${formatMs(message.latency_ms)}` : ""}
                  {message.interrupted ? " · interrupted" : ""}
                </span>
                <p className="text-sm whitespace-pre-wrap">{message.text}</p>
              </li>
            ))}
          </ol>
        )}
      </Disclosure>

      <Disclosure
        title="Tool calls and raw audit log"
        meta={`${plural(data.tool_calls.length, "tool call", "tool calls")}, ${plural(
          data.audit.length,
          "event",
          "events",
        )}`}
      >
        <div className="flex flex-col gap-4">
          <ul className="flex flex-col divide-y-[0.5px] divide-line">
            {data.tool_calls.map((tool, index) => (
              <li key={index} className="py-2 text-sm first:pt-0">
                <details>
                  <summary className="flex cursor-pointer flex-wrap items-center gap-2 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
                    <code className="font-medium">{tool.name}</code>
                    <StatusChip tone={tool.status === "ok" ? "good" : "critical"}>
                      {tool.status === "ok" ? "OK" : "Error"}
                    </StatusChip>
                    <span className="text-xs text-muted-foreground">
                      {formatMs(tool.duration_ms)} · {formatTimeSeconds(tool.created_at)}
                    </span>
                  </summary>
                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    <Json label="Arguments" value={tool.args} />
                    <Json label="Result" value={tool.result} />
                  </div>
                </details>
              </li>
            ))}
          </ul>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr className="border-b-[0.5px] border-line">
                  <th className="py-2 pr-3 font-medium">Time</th>
                  <th className="py-2 pr-3 font-medium">Event</th>
                  <th className="py-2 pr-3 font-medium">Actor</th>
                  <th className="py-2 font-medium">Details</th>
                </tr>
              </thead>
              <tbody>
                {data.audit.map((entry, index) => (
                  <tr key={index} className="border-b-[0.5px] border-line align-top last:border-0">
                    <td className="py-2 pr-3 whitespace-nowrap tabular-nums">
                      {formatTimeSeconds(entry.created_at)}
                    </td>
                    <td className="py-2 pr-3">
                      <code>{entry.event}</code>
                    </td>
                    <td className="py-2 pr-3">{entry.actor}</td>
                    <td className="py-2 font-mono text-xs break-all text-muted-foreground">
                      {JSON.stringify(entry.payload)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Disclosure>
    </>
  );
}

function Detail({
  label,
  value,
  hideEmpty = false,
}: {
  label: string;
  value: string | null;
  /** Leave the row out when the field was never recorded. */
  hideEmpty?: boolean;
}) {
  if (value === null && hideEmpty) return null;
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("break-words", value === null && "text-muted-foreground")}>
        {value ?? "Not recorded"}
      </dd>
    </>
  );
}

function Json({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div aria-busy aria-label="Loading the call" className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="w-72" />
      </div>
      <Panel title="Booking">
        <Skeleton className="h-6 w-64" />
        {[0, 1, 2].map((row) => (
          <Skeleton key={row} className="w-full" />
        ))}
      </Panel>
      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title="Caller details">
          <Skeleton className="w-full" />
          <Skeleton className="w-3/4" />
        </Panel>
        <Panel title="What happened">
          <Skeleton className="w-full" />
          <Skeleton className="w-3/4" />
        </Panel>
      </div>
    </div>
  );
}
