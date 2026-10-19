"use client";

import { LoaderCircle, ShieldCheck, UserRoundCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Meter, StatTile } from "@/components/team/figures";
import { StatusChip } from "@/components/team/status-chip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useTeamResource, useTeamSession } from "@/hooks/use-team-session";
import { formatCount, formatMs, formatTime, formatUsd } from "@/lib/format";
import { type HandoffRow, type Summary, hasRole } from "@/lib/team-api";

const GUARDRAILS = [
  {
    title: "Nothing is booked without the caller's OK",
    detail: "request_booking only creates a pending ticket. The backend books after Approve.",
  },
  {
    title: "Every write has an idempotency key",
    detail: "CRM and calendar steps are recorded with unique keys, so a retry never duplicates.",
  },
  {
    title: "No prices, coverage advice or approval promises",
    detail: "The policy monitor checks every reply, flags the call, and has Maya correct herself.",
  },
  {
    title: "Only real bookings are announced",
    detail: "A claim of a booking without a confirmed result is flagged and corrected.",
  },
  {
    title: "Sensitive details are refused",
    detail: "Tool schemas reject license, SSN, VIN, payment card and date-of-birth data.",
  },
  {
    title: "History only after verification",
    detail: "A 6-digit code, 3 tries, 5 minutes. Only the backend marks a caller verified.",
  },
  {
    title: "Hand off when it matters",
    detail:
      "Upset callers, claims and complaints, requests for a person, repeated out-of-scope " +
      "requests or corrections, failing tools, failed verification, incomplete bookings.",
  },
  {
    title: "Caps and an append-only audit log",
    detail: "Call length, turns and calls per day are capped; the audit log rejects edits.",
  },
];

export function OverviewPage() {
  const { user, api } = useTeamSession();
  const summary = useTeamResource<Summary>("/api/control-room/summary", 10_000);
  const queue = useTeamResource<HandoffRow[]>("/api/handoffs", 5_000);
  const [takingOver, setTakingOver] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const canTakeOver = hasRole(user, "agent");
  const metrics = summary.data?.metrics;
  const usage = summary.data?.usage;

  async function takeOver(handoff: HandoffRow) {
    setTakingOver(handoff.id);
    setActionError(null);
    try {
      await api(`/api/handoffs/${handoff.id}/take-over`, { method: "POST" });
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not take over the call.");
    } finally {
      setTakingOver(null);
      queue.reload();
      summary.reload();
    }
  }

  return (
    <>
      <h1 className="sr-only">Control room overview</h1>
      {summary.error && (
        <Alert variant="destructive">
          <AlertDescription>{summary.error}</AlertDescription>
        </Alert>
      )}

      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Calls today" value={metrics ? formatCount(metrics.calls_today) : null} />
        <StatTile label="Booked" value={metrics ? formatCount(metrics.booked_today) : null} />
        <StatTile
          label="Need a human"
          value={metrics ? formatCount(metrics.needs_human) : null}
          note={metrics && metrics.needs_human > 0 ? "Waiting in the queue" : undefined}
        />
        <StatTile
          label="Policy flags"
          value={metrics ? formatCount(metrics.policy_flags_today) : null}
          note="Today"
        />
      </dl>

      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <Card>
          <CardHeader>
            <CardTitle>Needs a human</CardTitle>
            <CardDescription>
              Calls the backend handed off, oldest first.
              {!canTakeOver && " Agents and admins can take over calls."}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {actionError && (
              <Alert variant="destructive">
                <AlertDescription>{actionError}</AlertDescription>
              </Alert>
            )}
            {queue.data === null ? (
              <p className="text-sm text-muted-foreground">{queue.error ?? "Loading..."}</p>
            ) : queue.data.length === 0 ? (
              <p className="text-sm text-muted-foreground">No one is waiting.</p>
            ) : (
              <ul className="flex flex-col divide-y">
                {queue.data.map((handoff) => (
                  <li
                    key={handoff.id}
                    className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0"
                  >
                    <div className="flex flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusChip tone="warning">{handoff.label}</StatusChip>
                        <StatusChip tone={handoff.call_active ? "good" : "neutral"}>
                          {handoff.call_active ? "Call live" : "Call ended"}
                        </StatusChip>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Call started {formatTime(handoff.call_started_at)}, handed off{" "}
                        {formatTime(handoff.created_at)}
                        {canTakeOver && (
                          <>
                            {" · "}
                            <Link
                              href={`/control-room/calls/${handoff.call_id}`}
                              className="underline underline-offset-2 hover:text-foreground"
                            >
                              Open call
                            </Link>
                          </>
                        )}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      disabled={!canTakeOver || takingOver !== null}
                      onClick={() => void takeOver(handoff)}
                    >
                      {takingOver === handoff.id ? (
                        <LoaderCircle className="motion-safe:animate-spin" />
                      ) : (
                        <UserRoundCheck />
                      )}
                      Take over call
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Usage</CardTitle>
            <CardDescription>Today, including evaluation runs that used the model.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-5">
            {usage ? (
              <Meter label="Calls vs daily cap" value={usage.calls_today} max={usage.daily_call_cap} />
            ) : (
              <p className="text-sm text-muted-foreground">{summary.error ?? "Loading..."}</p>
            )}
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
              <UsageRow
                label="Estimated cost"
                value={usage ? formatUsd(usage.estimated_cost_usd_today) : null}
                title="At paid-tier audio rates. The free tier is not billed."
              />
              <UsageRow
                label="Audio in / out"
                value={
                  usage
                    ? `${usage.audio_minutes_in_today} / ${usage.audio_minutes_out_today} min`
                    : null
                }
              />
              <UsageRow
                label="Latency p50"
                value={usage ? formatMs(usage.latency_p50_ms) : null}
                title="Caller's last words to Maya's first audio, live calls today."
              />
              <UsageRow label="Latency p95" value={usage ? formatMs(usage.latency_p95_ms) : null} />
            </dl>
            {usage && (
              <p className="text-xs text-muted-foreground">
                Latency from {usage.latency_samples.toLocaleString()} replies today.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Guardrails</CardTitle>
          <CardDescription>
            Settings here only tune the agent. The backend enforces every rule.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <ul className="grid gap-3 md:grid-cols-2">
            {GUARDRAILS.map((rule) => (
              <li key={rule.title} className="flex flex-col gap-1.5 rounded-lg border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium">{rule.title}</span>
                  <Badge variant="secondary">
                    <ShieldCheck aria-hidden />
                    Enforced in code
                  </Badge>
                </div>
                <p className="text-sm text-muted-foreground">{rule.detail}</p>
              </li>
            ))}
          </ul>
          <p className="rounded-lg bg-muted px-3 py-2 text-sm">
            Speech can&rsquo;t be blocked before it plays, so policy violations are detected,
            flagged, and corrected.
          </p>
          {summary.data && (
            <div className="flex flex-col gap-2">
              <h2 className="text-sm font-medium">Current settings</h2>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
                <UsageRow label="Model" value={summary.data.settings.model} />
                <UsageRow label="Integrations" value={summary.data.settings.integrations_mode} />
                <UsageRow
                  label="Max call length"
                  value={`${summary.data.settings.max_call_seconds} s`}
                />
                <UsageRow label="Max turns" value={String(summary.data.settings.max_turns)} />
                <UsageRow label="Daily call cap" value={String(summary.data.settings.daily_call_cap)} />
                <UsageRow
                  label="Silence ends a turn"
                  value={`${summary.data.settings.vad_silence_duration_ms} ms`}
                />
                <UsageRow
                  label="Ticket expires after"
                  value={`${summary.data.settings.approval_ttl_seconds} s`}
                />
                <UsageRow label="Time zone" value={summary.data.settings.business_timezone} />
              </dl>
              <p className="text-xs text-muted-foreground">
                Change these in <code>backend/.env</code> and restart the backend.
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}

function UsageRow({ label, value, title }: { label: string; value: string | null; title?: string }) {
  return (
    <div title={title}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value ?? "--"}</dd>
    </div>
  );
}
