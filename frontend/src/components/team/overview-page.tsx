"use client";

import { LoaderCircle, Phone, PhoneOff, Settings2, ShieldCheck, UserRoundCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { CollapsibleRule } from "@/components/team/collapsible-rule";
import { Meter } from "@/components/team/figures";
import { MetricCell, MetricStrip } from "@/components/team/metric-cell";
import { ErrorState, Skeleton } from "@/components/team/page-states";
import { Panel } from "@/components/team/panel";
import { StatusBar } from "@/components/team/status-bar";
import { StatusChip } from "@/components/team/status-chip";
import { Button } from "@/components/ui/button";
import { useTeamResource, useTeamSession } from "@/hooks/use-team-session";
import { useNow } from "@/lib/clock";
import {
  formatCount,
  formatElapsed,
  formatExact,
  formatMinutes,
  formatMs,
  formatUsd,
  plural,
} from "@/lib/format";
import { type HandoffRow, type Summary, hasRole } from "@/lib/team-api";

type Settings = Summary["settings"];
type Resource<T> = { data: T | null; error: string | null; reload: () => void };

// What the backend enforces, described in words. Numbers come from the summary's settings.
const RULES: { title: string; detail: (settings: Settings | null) => string }[] = [
  {
    title: "Nothing is booked without the caller's OK",
    detail: (s) =>
      `request_booking only creates a pending ticket${
        s ? `, which expires after ${s.approval_ttl_seconds.toLocaleString()} s` : ""
      }. The backend books only after the caller presses Approve.`,
  },
  {
    title: "Every write has an idempotency key",
    detail: () =>
      "CRM and calendar steps are recorded with unique keys, so a retry never creates a duplicate.",
  },
  {
    title: "No prices, coverage advice or approval promises",
    detail: () =>
      "The policy monitor checks every reply, flags the call, and has Maya correct herself. " +
      "Speech can't be blocked before it plays, so policy violations are detected, flagged, " +
      "and corrected.",
  },
  {
    title: "Only real bookings are announced",
    detail: () => "A claim of a booking without a confirmed result is flagged and corrected.",
  },
  {
    title: "Sensitive details are refused",
    detail: () =>
      "Tool schemas reject driver's license, Social Security, VIN, payment card and " +
      "date-of-birth data.",
  },
  {
    title: "History only after verification",
    detail: () =>
      "Returning callers confirm who they are with a one-time code with limited attempts and " +
      "a short expiry. Only the backend marks a caller verified.",
  },
  {
    title: "Hand off when it matters",
    detail: () =>
      "Upset callers, claims and complaints, requests for a person, repeated out-of-scope " +
      "requests or corrections, failing tools, failed verification, and incomplete bookings " +
      "go to a person.",
  },
  {
    title: "Caps and an append-only audit log",
    detail: (s) =>
      s
        ? `Calls end after ${s.max_call_seconds.toLocaleString()} s or ` +
          `${plural(s.max_turns, "caller turn", "caller turns")}, and at most ` +
          `${plural(s.daily_call_cap, "call starts", "calls start")} each day. ` +
          "The audit log rejects edits."
        : "Call length, turns and calls per day are capped. The audit log rejects edits.",
  },
];

export function OverviewPage() {
  const summary = useTeamResource<Summary>("/api/control-room/summary", 10_000);
  const queue = useTeamResource<HandoffRow[]>("/api/handoffs", 5_000);
  const metrics = summary.data?.metrics ?? null;
  // A refresh failed but older data is still on screen.
  const staleError = (summary.data && summary.error) || (queue.data && queue.error);

  return (
    <>
      <h1 className="sr-only">Control room overview</h1>

      {summary.data === null && summary.error ? (
        <ErrorState
          message={`Couldn't load today's numbers. ${summary.error}`}
          onRetry={summary.reload}
        />
      ) : (
        <MetricStrip label="Today" className="md:grid-cols-4">
          <MetricCell
            label="Calls today"
            value={metrics && formatCount(metrics.calls_today)}
            caption="live calls"
          />
          <MetricCell
            label="Booked"
            value={metrics && formatCount(metrics.booked_today)}
            caption={metrics && `of ${plural(metrics.calls_today, "call", "calls")}`}
          />
          <MetricCell
            label="Need a human"
            value={metrics && formatCount(metrics.needs_human)}
            caption={metrics && (metrics.needs_human === 0 ? "none waiting" : "waiting now")}
            tone={metrics && metrics.needs_human > 0 ? "warning" : "neutral"}
          />
          <MetricCell
            label="Policy flags"
            value={metrics && formatCount(metrics.policy_flags_today)}
            caption={metrics && (metrics.policy_flags_today === 0 ? "none today" : "today")}
            tone={metrics && metrics.policy_flags_today > 0 ? "warning" : "neutral"}
          />
        </MetricStrip>
      )}

      {staleError && (
        <ErrorState
          compact
          message={`Couldn't refresh. Showing the last numbers loaded. ${staleError}`}
          onRetry={() => {
            summary.reload();
            queue.reload();
          }}
        />
      )}

      {metrics?.calls_today === 0 && (
        <p className="text-sm text-muted-foreground">
          No live calls yet today.{" "}
          <Link href="/" className="underline underline-offset-4 hover:text-foreground">
            Start one from the customer view
          </Link>{" "}
          to see it here.
        </p>
      )}

      <HandoffQueue queue={queue} onChanged={summary.reload} />

      <div className="grid gap-6 md:grid-cols-2 md:items-start">
        <UsagePanel summary={summary} />
        <GuardrailsPanel settings={summary.data?.settings ?? null} />
      </div>
    </>
  );
}

function HandoffQueue({
  queue,
  onChanged,
}: {
  queue: Resource<HandoffRow[]>;
  onChanged: () => void;
}) {
  const { user, api } = useTeamSession();
  const now = useNow();
  const [takingOver, setTakingOver] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const canAct = hasRole(user, "agent");

  if (queue.data === null) {
    return queue.error ? (
      <ErrorState
        message={`Couldn't load the handoff queue. ${queue.error}`}
        onRetry={queue.reload}
      />
    ) : (
      <StatusBar tone="loading" title="Checking the handoff queue" />
    );
  }
  if (queue.data.length === 0) {
    return <StatusBar tone="good" title="All clear. Nobody is waiting for a human." />;
  }

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
      onChanged();
    }
  }

  return (
    <StatusBar
      tone="warning"
      title={`${plural(queue.data.length, "call is", "calls are")} waiting for a human`}
    >
      {actionError && (
        <p role="alert" className="text-sm text-destructive">
          {actionError}
        </p>
      )}
      <ul
        aria-label="Waiting for a human"
        className="flex flex-col divide-y-[0.5px] divide-line rounded-md border-[0.5px] border-line bg-card"
      >
        {queue.data.map((handoff) => (
          <li key={handoff.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
            <StatusChip tone="warning">{handoff.label}</StatusChip>
            <StatusChip
              tone="neutral"
              icon={
                handoff.call_active ? (
                  <Phone aria-hidden className="size-3" />
                ) : (
                  <PhoneOff aria-hidden className="size-3" />
                )
              }
            >
              {handoff.call_active ? "Call live" : "Call ended"}
            </StatusChip>
            <span className="text-sm text-muted-foreground" title={formatExact(handoff.created_at)}>
              Waiting {formatElapsed(handoff.created_at, now)}
            </span>
            {canAct && (
              <div className="ml-auto flex items-center gap-3">
                <Link
                  href={`/control-room/calls/${handoff.call_id}`}
                  className="rounded-md text-sm underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  Open call
                </Link>
                <Button
                  size="sm"
                  disabled={takingOver !== null}
                  onClick={() => void takeOver(handoff)}
                >
                  {takingOver === handoff.id ? (
                    <LoaderCircle className="motion-safe:animate-spin" />
                  ) : (
                    <UserRoundCheck />
                  )}
                  Take over
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      {!canAct && (
        <p className="text-xs text-muted-foreground">
          Agents and admins can open and take over calls.
        </p>
      )}
    </StatusBar>
  );
}

function UsagePanel({ summary }: { summary: Resource<Summary> }) {
  const usage = summary.data?.usage ?? null;
  return (
    <Panel
      title="Usage today"
      footer={usage && `From ${plural(usage.latency_samples, "reply", "replies")} today`}
    >
      {usage === null ? (
        summary.error ? (
          // The metrics strip above shows the error and its Retry button.
          <p className="text-sm text-muted-foreground">Usage didn&rsquo;t load.</p>
        ) : (
          <div className="flex flex-col gap-3" aria-busy>
            <Skeleton className="w-40" />
            <Skeleton className="h-2 w-full" />
            {[0, 1, 2, 3].map((row) => (
              <Skeleton key={row} className="w-full" />
            ))}
          </div>
        )
      ) : (
        <>
          <div className="flex flex-col gap-1">
            <Meter label="Model sessions" value={usage.calls_today} max={usage.daily_call_cap} />
            <p className="text-xs text-muted-foreground">
              Includes live evaluation runs, not only live calls.
            </p>
          </div>
          <dl className="flex flex-col divide-y-[0.5px] divide-line text-sm">
            <UsageRow
              label="Estimated cost"
              value={formatUsd(usage.estimated_cost_usd_today)}
              title="At paid-tier audio rates. The free tier is not billed."
            />
            <UsageRow
              label="Audio in / out"
              value={`${formatMinutes(usage.audio_minutes_in_today)} / ${formatMinutes(
                usage.audio_minutes_out_today,
              )}`}
            />
            <UsageRow
              label="Latency p50"
              value={usage.latency_p50_ms === null ? "Not recorded" : formatMs(usage.latency_p50_ms)}
              title="From the caller's last words to Maya's first audio, live calls today."
            />
            <UsageRow
              label="Latency p95"
              value={usage.latency_p95_ms === null ? "Not recorded" : formatMs(usage.latency_p95_ms)}
            />
          </dl>
        </>
      )}
    </Panel>
  );
}

function UsageRow({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2" title={title}>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function GuardrailsPanel({ settings }: { settings: Settings | null }) {
  return (
    <Panel
      title="Guardrails"
      action={
        <StatusChip tone="neutral" icon={<ShieldCheck aria-hidden className="size-3" />}>
          Enforced in code
        </StatusChip>
      }
      footer={
        <>
          <p>Settings here only tune the agent. The backend enforces every rule.</p>
          <p>This list documents rules in the code. It is not a live status check.</p>
        </>
      }
    >
      <div className="-my-3">
        {RULES.map((rule) => (
          <CollapsibleRule key={rule.title} title={rule.title}>
            {rule.detail(settings)}
          </CollapsibleRule>
        ))}
        <CollapsibleRule title="Current settings" icon={<Settings2 className="size-4" />}>
          {settings ? (
            <div className="flex flex-col gap-2">
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2">
                <SettingRow label="Model" value={settings.model} />
                <SettingRow label="Integrations" value={settings.integrations_mode} />
                <SettingRow
                  label="Max call length"
                  value={`${settings.max_call_seconds.toLocaleString()} s`}
                />
                <SettingRow label="Max turns" value={formatCount(settings.max_turns)} />
                <SettingRow label="Daily call cap" value={formatCount(settings.daily_call_cap)} />
                <SettingRow
                  label="Silence ends a turn"
                  value={formatMs(settings.vad_silence_duration_ms)}
                />
                <SettingRow
                  label="Ticket expires after"
                  value={`${settings.approval_ttl_seconds.toLocaleString()} s`}
                />
                <SettingRow label="Business time zone" value={settings.business_timezone} />
              </dl>
              <p className="text-xs">
                Change these in <code>backend/.env</code> and restart the backend.
              </p>
            </div>
          ) : (
            <Skeleton className="w-full" />
          )}
        </CollapsibleRule>
      </div>
    </Panel>
  );
}

function SettingRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs">{label}</dt>
      <dd className="text-sm text-foreground">{value}</dd>
    </div>
  );
}
