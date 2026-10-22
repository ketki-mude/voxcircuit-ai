import { Suspense } from "react";

import { CallsList, CallsListFallback } from "@/components/team/calls-list";

/** Master-detail: the calls list stays mounted while the selected call changes. */
export default function CallsLayout({ children }: LayoutProps<"/control-room/calls">) {
  return (
    <>
      <h1 className="sr-only">Calls</h1>
      <div className="grid gap-6 lg:grid-cols-[20rem_minmax(0,1fr)] lg:items-start">
        {/* The list reads the selected call from the URL, which is known at request time. */}
        <Suspense fallback={<CallsListFallback />}>
          <CallsList />
        </Suspense>
        <div className="flex min-w-0 flex-col gap-6">{children}</div>
      </div>
    </>
  );
}
