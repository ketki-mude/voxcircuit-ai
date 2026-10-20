"use client";

import { FlaskConical, LoaderCircle, Radio } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { StatTile } from "@/components/team/figures";
import { StatusChip } from "@/components/team/status-chip";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useTeamResource, useTeamSession } from "@/hooks/use-team-session";
import { formatCount, formatDateTime, formatMs, formatPercent } from "@/lib/format";
import { type EvaluationRun, type RunDetail, hasRole } from "@/lib/team-api";
import { cn } from "@/lib/utils";

export function EvaluationPage() {
  const { user, api } = useTeamSession();
  const [selected, setSelected] = useState<string | null>(null);
  const [starting, setStarting] = useState<"scripted" | "live" | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  const runs = useTeamResource<EvaluationRun[]>("/api/evaluation/runs", 3_000);
  const runId = selected ?? runs.data?.[0]?.id ?? null;
  const running = runs.data?.some((run) => run.status === "running") ?? false;
  const detail = useTeamResource<RunDetail>(
    runId ? `/api/evaluation/runs/${runId}` : null,
    running ? 2_000 : 0,
  );
  const canRun = hasRole(user, "admin");
  const canOpenCalls = hasRole(user, "agent");
  const run = detail.data?.run;

  async function start(mode: "scripted" | "live") {
    setStarting(mode);
    setStartError(null);
    try {
      const { run_id } = await api<{ run_id: string }>("/api/evaluate", {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      setSelected(run_id);
      runs.reload();
    } catch (error) {
      setStartError(error instanceof Error ? error.message : "Could not start the evaluation.");
    } finally {
      setStarting(null);
    }
  }

  return (
    <>
      <Card>
        <CardHeader className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <CardTitle>
              <h1>Evaluation</h1>
            </CardTitle>
            <CardDescription>
              Scripted runs play 30 scenarios through the real backend with no network. Live runs
              send about 5 scenarios to the real model and count against the daily call cap.
            </CardDescription>
          </div>
          {canRun ? (
            <div className="flex flex-wrap gap-2">
              <Button disabled={running || starting !== null} onClick={() => void start("scripted")}>
                {starting === "scripted" ? <LoaderCircle className="motion-safe:animate-spin" /> : <FlaskConical />}
                Run scripted evaluation
              </Button>
              <Button
                variant="outline"
                disabled={running || starting !== null}
                onClick={() => void start("live")}
              >
                {starting === "live" ? <LoaderCircle className="motion-safe:animate-spin" /> : <Radio />}
                Run live evaluation
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Admins can start runs.</p>
          )}
        </CardHeader>
        {startError && (
          <CardContent>
            <Alert variant="destructive">
              <AlertDescription>{startError}</AlertDescription>
            </Alert>
          </CardContent>
        )}
      </Card>

      {runs.data !== null && runs.data.length === 0 && (
        <p className="text-sm text-muted-foreground">No evaluation runs yet.</p>
      )}

      {run && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-heading text-lg font-semibold">
              {run.mode === "live" ? "Live" : "Scripted"} run, {formatDateTime(run.started_at)}
            </h2>
            <RunStatus run={run} />
          </div>
          {run.error && (
            <Alert variant="destructive">
              <AlertDescription>{run.error}</AlertDescription>
            </Alert>
          )}
          <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <StatTile
              label="Task success"
              value={formatPercent(run.task_success_rate)}
              note={`${run.passed} of ${run.scenario_count} scenarios`}
            />
            <StatTile label="Handoff accuracy" value={formatPercent(run.handoff_accuracy)} />
            <StatTile
              label="Unauthorized writes"
              value={formatCount(run.unauthorized_writes)}
              note={<MustBeZero value={run.unauthorized_writes} />}
            />
            <StatTile
              label="History leaks"
              value={formatCount(run.history_leaks)}
              note={<MustBeZero value={run.history_leaks} />}
            />
            <StatTile label="Policy flags" value={formatCount(run.policy_flags)} />
            <StatTile
              label="Latency p50 / p95"
              value={
                run.latency_p50_ms === null
                  ? null
                  : `${run.latency_p50_ms.toLocaleString()} / ${formatMs(run.latency_p95_ms)}`
              }
              note={run.mode === "scripted" ? "Scripted replies are instant" : undefined}
            />
          </dl>

          <Card>
            <CardHeader>
              <CardTitle>Scenarios</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              {detail.data && detail.data.results.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {run.status === "running" ? "Running..." : "No results."}
                </p>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted-foreground">
                    <tr className="border-b">
                      <th className="py-2 pr-3 font-medium">Scenario</th>
                      <th className="py-2 pr-3 font-medium">Result</th>
                      <th className="py-2 pr-3 font-medium">What happened</th>
                      <th className="py-2 text-right font-medium">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.data?.results.map((result) => (
                      <tr key={result.scenario_id} className="border-b align-top last:border-0">
                        <td className="py-2 pr-3">
                          <div className="font-medium">{result.title}</div>
                          <code className="text-xs text-muted-foreground">{result.scenario_id}</code>
                        </td>
                        <td className="py-2 pr-3">
                          <StatusChip tone={result.passed ? "good" : "critical"}>
                            {result.passed ? "Passed" : "Failed"}
                          </StatusChip>
                        </td>
                        <td className="py-2 pr-3 text-muted-foreground">
                          {result.failures.length > 0 ? (
                            <ul className="list-disc pl-4 text-destructive">
                              {result.failures.map((failure) => (
                                <li key={failure}>{failure}</li>
                              ))}
                            </ul>
                          ) : (
                            describe(result.actual)
                          )}
                          {result.call_id && canOpenCalls && (
                            <>
                              {" "}
                              <Link
                                href={`/control-room/calls/${result.call_id}`}
                                className="underline underline-offset-2 hover:text-foreground"
                              >
                                Open call
                              </Link>
                            </>
                          )}
                        </td>
                        <td className="py-2 text-right tabular-nums">{result.duration_ms} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </CardContent>
          </Card>
        </>
      )}

      {runs.data && runs.data.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Runs</CardTitle>
            <CardDescription>The latest 20. Select one to see its scenarios.</CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr className="border-b">
                  <th className="py-2 pr-3 font-medium">Started</th>
                  <th className="py-2 pr-3 font-medium">Mode</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 text-right font-medium">Passed</th>
                  <th className="py-2 pr-3 text-right font-medium">Task success</th>
                  <th className="py-2 font-medium">Started by</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {runs.data.map((item) => (
                  <tr
                    key={item.id}
                    className={cn("border-b last:border-0", item.id === runId && "bg-muted/60")}
                  >
                    <td className="py-2 pr-3">
                      <button
                        type="button"
                        onClick={() => setSelected(item.id)}
                        className="font-medium underline underline-offset-2"
                        aria-current={item.id === runId ? "true" : undefined}
                      >
                        {formatDateTime(item.started_at)}
                      </button>
                    </td>
                    <td className="py-2 pr-3">{item.mode}</td>
                    <td className="py-2 pr-3">
                      <RunStatus run={item} />
                    </td>
                    <td className="py-2 pr-3 text-right">
                      {item.passed} / {item.scenario_count}
                    </td>
                    <td className="py-2 pr-3 text-right">{formatPercent(item.task_success_rate)}</td>
                    <td className="py-2">{item.requested_by ?? "Command line"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </>
  );
}

function RunStatus({ run }: { run: EvaluationRun }) {
  if (run.status === "running") return <StatusChip tone="neutral">Running</StatusChip>;
  if (run.status === "failed") return <StatusChip tone="critical">Failed</StatusChip>;
  return run.passed === run.scenario_count ? (
    <StatusChip tone="good">All passed</StatusChip>
  ) : (
    <StatusChip tone="warning">{run.scenario_count - run.passed} failed</StatusChip>
  );
}

function MustBeZero({ value }: { value: number | null }) {
  if (value === null) return <>Must be 0</>;
  return value === 0 ? (
    <StatusChip tone="good">0, as required</StatusChip>
  ) : (
    <StatusChip tone="critical">Must be 0</StatusChip>
  );
}

function describe(actual: Record<string, unknown>): string {
  const parts = [`booking ${String(actual.booking ?? "--").replaceAll("_", " ")}`];
  if (actual.handoff && actual.handoff !== "none") {
    parts.push(`handoff: ${String(actual.handoff).replaceAll("_", " ")}`);
  }
  const policy = actual.policy as string[] | undefined;
  if (policy?.length) parts.push(`flagged: ${policy.join(", ").replaceAll("_", " ")}`);
  if (actual.verified) parts.push("verified");
  return parts.join(" · ");
}
