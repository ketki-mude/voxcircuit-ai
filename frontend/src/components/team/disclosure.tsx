"use client";

import { Collapsible } from "@base-ui/react/collapsible";
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

/** A flat card that starts closed: a title row that opens to show its content. */
export function Disclosure({
  title,
  meta,
  children,
}: {
  title: string;
  /** Short text beside the chevron, such as a count. */
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Collapsible.Root className="rounded-lg border-[0.5px] border-line bg-card">
      <Collapsible.Trigger className="group flex w-full items-center justify-between gap-3 rounded-lg px-4 py-3 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
        <span className="text-sm font-semibold">{title}</span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          {meta}
          <ChevronDown
            aria-hidden
            className="size-4 transition-transform group-data-panel-open:rotate-180"
          />
        </span>
      </Collapsible.Trigger>
      <Collapsible.Panel className="border-t-[0.5px] border-line p-4">{children}</Collapsible.Panel>
    </Collapsible.Root>
  );
}
