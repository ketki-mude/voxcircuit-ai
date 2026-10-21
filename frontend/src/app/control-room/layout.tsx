import type { Metadata } from "next";
import { Suspense } from "react";

import { TeamHeader } from "@/components/team/team-header";
import { TeamShell } from "@/components/team/team-shell";
import { TeamSessionProvider } from "@/hooks/use-team-session";

export const metadata: Metadata = {
  title: "Control room | VoxCircuit AI",
};

export default function ControlRoomLayout({ children }: LayoutProps<"/control-room">) {
  return (
    <TeamSessionProvider>
      <TeamHeader />
      {/* Client-rendered behind sign-in; route data streams in at request time. */}
      <Suspense fallback={<main className="flex-1" aria-busy />}>
        <TeamShell>{children}</TeamShell>
      </Suspense>
    </TeamSessionProvider>
  );
}
