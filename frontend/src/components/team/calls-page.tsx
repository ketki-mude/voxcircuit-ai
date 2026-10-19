"use client";

import Link from "next/link";
import { useState } from "react";

import { StatusChip } from "@/components/team/status-chip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useTeamResource, useTeamSession } from "@/hooks/use-team-session";
import { formatDateTime, formatSeconds, formatUsd } from "@/lib/format";
import { type CallRow, hasRole } from "@/lib/team-api";
import { cn } from "@/lib/utils";

const FILTERS = [
  { value: "live", label: "Live" },
  { value: "evaluation", label: "Evaluation" },
  { value: "all", label: "All" },
] as const;

export function CallsPage() {
  const { user } = useTeamSession();
  const [purpose, setPurpose] = useState<(typeof FILTERS)[number]["value"]>("live");
  const calls = useTeamResource<CallRow[]>(`/api/calls?purpose=${purpose}&limit=100`, 10_000);
  const canOpen = hasRole(user, "agent");

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <CardTitle>
            <h1>Calls</h1>
          </CardTitle>
          <CardDescription>
            Newest first.{" "}
            {canOpen ? "Open a call to read its transcript." : "Agents can open transcripts."}
          </CardDescription>
        </div>
        <div role="group" aria-label="Which calls" className="flex rounded-lg border bg-muted p-0.5 text-sm">
          {FILTERS.map((filter) => (
            <button
              key={filter.value}
              type="button"
              aria-pressed={purpose === filter.value}
              onClick={() => setPurpose(filter.value)}
              className={cn(
                "rounded-md px-3 py-1",
                purpose === filter.value ? "bg-card font-medium shadow-sm" : "text-muted-foreground",
              )}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {calls.error && (
          <Alert variant="destructive" className="mb-3">
            <AlertDescription>{calls.error}</AlertDescription>
          </Alert>
        )}
        {calls.data === null ? (
          <p className="text-sm text-muted-foreground">{calls.error ? "" : "Loading..."}</p>
        ) : calls.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No calls yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr className="border-b">
                  <th className="py-2 pr-3 font-medium">Started</th>
                  <th className="py-2 pr-3 font-medium">Length</th>
                  <th className="py-2 pr-3 font-medium">Mode</th>
                  <th className="py-2 pr-3 font-medium">Ended</th>
                  <th className="py-2 pr-3 text-right font-medium">Turns</th>
                  <th className="py-2 pr-3 font-medium">Outcome</th>
                  <th className="py-2 text-right font-medium">Est. cost</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {calls.data.map((call) => (
                  <tr key={call.id} className="border-b last:border-0">
                    <td className="py-2 pr-3 whitespace-nowrap">
                      {canOpen ? (
                        <Link
                          href={`/control-room/calls/${call.id}`}
                          className="font-medium underline underline-offset-2"
                        >
                          {formatDateTime(call.started_at)}
                        </Link>
                      ) : (
                        formatDateTime(call.started_at)
                      )}
                    </td>
                    <td className="py-2 pr-3">{formatSeconds(call.duration_seconds)}</td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      {call.input_mode === "audio" ? "Voice" : "Text"} · {call.engine}
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap">
                      {call.status === "active" ? "In progress" : (call.end_reason ?? call.status).replaceAll("_", " ")}
                    </td>
                    <td className="py-2 pr-3 text-right">{call.user_turns}</td>
                    <td className="py-2 pr-3">
                      <div className="flex flex-wrap gap-1">
                        {call.booked && <StatusChip tone="good">Booked</StatusChip>}
                        {call.handoff_reason && (
                          <StatusChip tone="warning">{call.handoff_reason.replaceAll("_", " ")}</StatusChip>
                        )}
                        {call.policy_flags > 0 && (
                          <StatusChip tone="critical">
                            {call.policy_flags} policy {call.policy_flags === 1 ? "flag" : "flags"}
                          </StatusChip>
                        )}
                        {call.status === "failed" && <StatusChip tone="critical">Failed</StatusChip>}
                      </div>
                    </td>
                    <td className="py-2 text-right">{formatUsd(call.estimated_cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
