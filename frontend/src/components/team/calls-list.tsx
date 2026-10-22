"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { type ReactNode, useState } from "react";

import { EmptyState, ErrorState, Skeleton } from "@/components/team/page-states";
import { Panel } from "@/components/team/panel";
import { StatusChip } from "@/components/team/status-chip";
import { Button } from "@/components/ui/button";
import { useTeamResource, useTeamSession } from "@/hooks/use-team-session";
import { listOutcomes, type ListOutcome, OUTCOME_CHIP, shortId } from "@/lib/call-facts";
import { useNow } from "@/lib/clock";
import { formatDateTime, formatExact, formatSeconds, formatTime } from "@/lib/format";
import { type CallRow, hasRole } from "@/lib/team-api";
import { cn } from "@/lib/utils";

const LIMIT = 100;

type Filter = "all" | Exclude<ListOutcome, "in_progress">;

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "booked", label: "Booked" },
  { value: "handed_off", label: "Handed off" },
  { value: "not_booked", label: "Not booked" },
];

function matches(row: CallRow, filter: Filter): boolean {
  return filter === "all" || listOutcomes(row).includes(filter);
}

const PANEL_CLASS = "lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)]";

/** The master list. The selected call is the one in the URL. */
export function CallsList() {
  const { id: selected } = useParams<{ id?: string }>();
  const { user } = useTeamSession();
  const canOpen = hasRole(user, "agent");
  const calls = useTeamResource<CallRow[]>(`/api/calls?purpose=live&limit=${LIMIT}`, 10_000);
  const [filter, setFilter] = useState<Filter>("all");
  const now = useNow();
  const rows = calls.data ?? [];
  const shown = rows.filter((row) => matches(row, filter));

  let body: ReactNode;
  if (calls.data === null) {
    body = calls.error ? (
      <ErrorState message={`Couldn't load calls. ${calls.error}`} onRetry={calls.reload} />
    ) : (
      <ListSkeleton />
    );
  } else if (rows.length === 0) {
    body = (
      <EmptyState
        title="No calls yet"
        detail="Calls show up here as they happen."
        action={
          <Link href="/" className="text-sm underline underline-offset-4 hover:text-foreground">
            Start one from the customer view
          </Link>
        }
      />
    );
  } else if (shown.length === 0) {
    body = (
      <EmptyState
        title="No calls match this filter"
        action={
          <Button variant="outline" size="sm" onClick={() => setFilter("all")}>
            Show all calls
          </Button>
        }
      />
    );
  } else {
    body = (
      <ul aria-label="Calls" className="-mx-4 flex flex-col divide-y-[0.5px] divide-line">
        {shown.map((row) => (
          <li key={row.id}>
            <CallRowItem
              row={row}
              selected={row.id === selected}
              canOpen={canOpen}
              sameDay={now > 0 && new Date(row.started_at).toDateString() === new Date(now).toDateString()}
            />
          </li>
        ))}
      </ul>
    );
  }

  return (
    <Panel
      title="Latest calls"
      className={cn(PANEL_CLASS, selected && "hidden lg:flex")}
      bodyClassName="min-h-0 overflow-y-auto"
      footer={`Live calls, newest first, up to ${LIMIT}. Evaluation calls open from the Evaluation tab.`}
    >
      {calls.data !== null && calls.error && (
        <ErrorState compact message="Couldn't refresh the list." onRetry={calls.reload} />
      )}
      <div role="group" aria-label="Filter by outcome" className="flex flex-wrap gap-1">
        {FILTERS.map((option) => {
          const count = rows.filter((row) => matches(row, option.value)).length;
          const active = filter === option.value;
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={active}
              onClick={() => setFilter(option.value)}
              className={cn(
                "rounded-md border-[0.5px] px-2 py-1 text-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                active
                  ? "border-primary/50 bg-primary/10 font-medium text-primary"
                  : "border-line text-muted-foreground hover:text-foreground",
              )}
            >
              {option.label}
              {calls.data !== null && <span className="ml-1 tabular-nums">{count}</span>}
            </button>
          );
        })}
      </div>
      {body}
    </Panel>
  );
}

function CallRowItem({
  row,
  selected,
  canOpen,
  sameDay,
}: {
  row: CallRow;
  selected: boolean;
  canOpen: boolean;
  sameDay: boolean;
}) {
  const content = (
    <>
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1">
          {listOutcomes(row).map((outcome) => (
            <StatusChip key={outcome} tone={OUTCOME_CHIP[outcome].tone}>
              {OUTCOME_CHIP[outcome].label}
            </StatusChip>
          ))}
        </div>
        <time
          dateTime={row.started_at}
          title={formatExact(row.started_at)}
          className="shrink-0 text-xs text-muted-foreground tabular-nums"
        >
          {sameDay ? formatTime(row.started_at) : formatDateTime(row.started_at)}
        </time>
      </div>
      <p className="text-xs text-muted-foreground">
        <span className="font-mono" title={row.id}>
          {shortId(row.id)}
        </span>
        {" · "}
        <span className="tabular-nums">
          {row.duration_seconds !== null
            ? formatSeconds(row.duration_seconds)
            : row.status === "active"
              ? "In progress"
              : "--"}
        </span>
      </p>
    </>
  );
  const base = "flex flex-col gap-1 px-4 py-3";
  if (!canOpen) return <div className={base}>{content}</div>;
  return (
    <Link
      href={`/control-room/calls/${row.id}`}
      aria-current={selected ? "page" : undefined}
      className={cn(
        base,
        "outline-none focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset",
        selected ? "bg-primary/10 shadow-[inset_2px_0_0_var(--color-primary)]" : "hover:bg-muted",
      )}
    >
      {content}
    </Link>
  );
}

function ListSkeleton() {
  return (
    <ul aria-busy aria-label="Loading calls" className="flex flex-col gap-4">
      {[0, 1, 2, 3, 4].map((row) => (
        <li key={row} className="flex flex-col gap-2">
          <div className="flex justify-between">
            <Skeleton className="h-4 w-20" />
            <Skeleton className="w-16" />
          </div>
          <Skeleton className="w-24" />
        </li>
      ))}
    </ul>
  );
}

/** Shown while the list's route data streams in. */
export function CallsListFallback() {
  return (
    <Panel title="Latest calls" className={PANEL_CLASS}>
      <ListSkeleton />
    </Panel>
  );
}
