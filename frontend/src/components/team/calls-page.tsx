"use client";

import { AgentsOnly } from "@/components/team/call-detail-page";
import { EmptyState } from "@/components/team/page-states";
import { Panel } from "@/components/team/panel";
import { useTeamSession } from "@/hooks/use-team-session";
import { hasRole } from "@/lib/team-api";

/** The detail side of the Calls page before a call is chosen. Hidden on narrow screens. */
export function CallsPage() {
  const { user } = useTeamSession();
  return (
    <div className="hidden lg:block">
      {hasRole(user, "agent") ? (
        <Panel>
          <EmptyState
            title="Choose a call"
            detail="Pick a call from the list to see what happened and its booking."
          />
        </Panel>
      ) : (
        <AgentsOnly />
      )}
    </div>
  );
}
