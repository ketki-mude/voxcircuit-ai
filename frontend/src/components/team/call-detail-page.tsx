"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";

import { StatTile } from "@/components/team/figures";
import { StatusChip } from "@/components/team/status-chip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTeamResource } from "@/hooks/use-team-session";
import { formatDateTime, formatMs, formatTime, formatUsd } from "@/lib/format";
import type { CallDetail } from "@/lib/team-api";
import { cn } from "@/lib/utils";

export function CallDetailPage() {
  const { id } = useParams<{ id: string }>();
  const detail = useTeamResource<CallDetail>(`/api/calls/${id}`, 5_000);
  const data = detail.data;

  return (
    <>
      <Link
        href="/control-room/calls"
        className="flex items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft aria-hidden className="size-4" />
        All calls
      </Link>
      {detail.error && (
        <Alert variant="destructive">
          <AlertDescription>{detail.error}</AlertDescription>
        </Alert>
      )}
      {!data ? (
        <p className="text-sm text-muted-foreground">{detail.error ? "" : "Loading..."}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-heading text-xl font-semibold">
              Call on {formatDateTime(data.call.started_at)}
            </h1>
            {data.call.status === "active" && <StatusChip tone="good">In progress</StatusChip>}
            {data.call.flagged && <StatusChip tone="warning">Flagged</StatusChip>}
            {data.call.verified && <StatusChip tone="good">Verified caller</StatusChip>}
            {data.call.purpose === "evaluation" && <StatusChip tone="neutral">Evaluation</StatusChip>}
          </div>
          <p className="text-sm text-muted-foreground">
            {data.call.input_mode === "audio" ? "Voice" : "Text"} call, {data.call.engine}
            {data.call.model ? ` (${data.call.model})` : ""}, ended{" "}
            {data.call.end_reason?.replaceAll("_", " ") ?? "not yet"}
            {data.call.ended_at ? ` at ${formatTime(data.call.ended_at)}` : ""}.
          </p>

          <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Latency p50" value={formatMs(data.usage.latency_p50_ms)} />
            <StatTile label="Latency p95" value={formatMs(data.usage.latency_p95_ms)} />
            <StatTile
              label="Audio in / out"
              value={`${Math.round(data.usage.audio_seconds_in)} / ${Math.round(data.usage.audio_seconds_out)} s`}
            />
            <StatTile label="Estimated cost" value={formatUsd(data.usage.estimated_cost_usd)} />
          </dl>

          <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
            <Card>
              <CardHeader>
                <CardTitle>Transcript</CardTitle>
              </CardHeader>
              <CardContent>
                {data.messages.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No messages stored.</p>
                ) : (
                  <ol className="flex flex-col gap-3">
                    {data.messages.map((message) => (
                      <li
                        key={message.seq}
                        className={cn(
                          "flex flex-col gap-1",
                          message.role === "user" ? "items-end" : "items-start",
                        )}
                      >
                        <span className="text-xs text-muted-foreground">
                          {message.role === "user" ? "Caller" : "Maya"} · {formatTime(message.created_at)}
                          {message.source === "typed" ? " · typed" : ""}
                          {message.latency_ms !== null ? ` · ${message.latency_ms} ms` : ""}
                          {message.interrupted ? " · interrupted" : ""}
                        </span>
                        <p
                          className={cn(
                            "max-w-[85%] rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap",
                            message.role === "user"
                              ? "rounded-br-sm bg-primary text-primary-foreground"
                              : "rounded-bl-sm bg-muted",
                          )}
                        >
                          {message.text}
                        </p>
                      </li>
                    ))}
                  </ol>
                )}
              </CardContent>
            </Card>

            <div className="flex flex-col gap-6">
              <Section title="Bookings" empty={data.approvals.length === 0}>
                {data.approvals.map((approval) => (
                  <div key={approval.id} className="flex flex-col gap-2 rounded-lg border p-3 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">
                        {approval.action === "reschedule" ? "Reschedule to" : "Book"} {approval.slot}
                      </span>
                      <StatusChip
                        tone={
                          approval.outcome?.success
                            ? "good"
                            : approval.status === "pending"
                              ? "warning"
                              : approval.status === "approved"
                                ? "critical"
                                : "neutral"
                        }
                      >
                        {approval.outcome
                          ? approval.outcome.success
                            ? "Booked"
                            : "Not booked"
                          : approval.status}
                      </StatusChip>
                    </div>
                    {approval.outcome && (
                      <p className="text-muted-foreground">{approval.outcome.summary}</p>
                    )}
                    {approval.steps.length > 0 && (
                      <ul className="flex flex-col gap-1">
                        {approval.steps.map((step) => (
                          <li key={step.step} className="flex flex-wrap items-center gap-2">
                            <StatusChip tone={step.status === "done" ? "good" : step.status === "failed" ? "critical" : "neutral"}>
                              {step.step === "crm_upsert" ? "CRM" : "Calendar"} {step.status}
                            </StatusChip>
                            <span className="text-xs text-muted-foreground">
                              {step.attempts} {step.attempts === 1 ? "attempt" : "attempts"} · key{" "}
                              <code>{step.idempotency_key}</code>
                              {step.last_error ? ` · ${step.last_error}` : ""}
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </Section>

              <Section title="Handoffs" empty={data.handoffs.length === 0}>
                {data.handoffs.map((handoff) => (
                  <p key={handoff.id} className="flex flex-wrap items-center gap-2 text-sm">
                    <StatusChip tone="warning">{handoff.label}</StatusChip>
                    <span className="text-muted-foreground">
                      {handoff.status === "taken"
                        ? `Taken by ${handoff.taken_by ?? "a team member"} at ${formatTime(handoff.taken_at)}`
                        : `Open since ${formatTime(handoff.created_at)}`}
                    </span>
                  </p>
                ))}
              </Section>

              <Section title="Policy flags" empty={data.policy_events.length === 0}>
                {data.policy_events.map((event, index) => (
                  <div key={index} className="flex flex-col gap-1 text-sm">
                    <StatusChip tone="critical">{event.rule.replaceAll("_", " ")}</StatusChip>
                    <q className="text-muted-foreground">{event.excerpt}</q>
                  </div>
                ))}
              </Section>

              <Section title="Verification" empty={data.verification.length === 0}>
                {data.verification.map((attempt, index) => (
                  <p key={index} className="flex items-center gap-2 text-sm">
                    <StatusChip
                      tone={attempt.status === "verified" ? "good" : attempt.status === "pending" ? "neutral" : "critical"}
                    >
                      {attempt.status}
                    </StatusChip>
                    <span className="text-muted-foreground">
                      {attempt.attempts} {attempt.attempts === 1 ? "try" : "tries"} ·{" "}
                      {formatTime(attempt.created_at)}
                    </span>
                  </p>
                ))}
              </Section>
            </div>
          </div>

          <Section title="Tool calls" empty={data.tool_calls.length === 0}>
            <ul className="flex flex-col divide-y">
              {data.tool_calls.map((tool, index) => (
                <li key={index} className="py-2 text-sm first:pt-0">
                  <details>
                    <summary className="flex cursor-pointer flex-wrap items-center gap-2">
                      <code className="font-medium">{tool.name}</code>
                      <StatusChip tone={tool.status === "ok" ? "good" : "critical"}>{tool.status}</StatusChip>
                      <span className="text-xs text-muted-foreground">
                        {tool.duration_ms} ms · {formatTime(tool.created_at)}
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
          </Section>

          <Section title="Audit log" empty={data.audit.length === 0}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted-foreground">
                  <tr className="border-b">
                    <th className="py-2 pr-3 font-medium">Time</th>
                    <th className="py-2 pr-3 font-medium">Event</th>
                    <th className="py-2 pr-3 font-medium">Actor</th>
                    <th className="py-2 font-medium">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {data.audit.map((entry, index) => (
                    <tr key={index} className="border-b align-top last:border-0">
                      <td className="py-1.5 pr-3 whitespace-nowrap tabular-nums">
                        {new Date(entry.created_at).toLocaleTimeString()}
                      </td>
                      <td className="py-1.5 pr-3">
                        <code>{entry.event}</code>
                      </td>
                      <td className="py-1.5 pr-3">{entry.actor}</td>
                      <td className="py-1.5 font-mono text-xs break-all text-muted-foreground">
                        {JSON.stringify(entry.payload)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      )}
    </>
  );
}

function Section({ title, empty, children }: { title: string; empty: boolean; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {empty ? <p className="text-sm text-muted-foreground">None.</p> : children}
      </CardContent>
    </Card>
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
