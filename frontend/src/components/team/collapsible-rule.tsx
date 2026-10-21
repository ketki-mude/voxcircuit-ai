"use client";

import { Collapsible } from "@base-ui/react/collapsible";
import { Check, ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

/** A row that shows its title and reveals its description when opened. */
export function CollapsibleRule({
  title,
  icon = <Check className="size-4" />,
  children,
}: {
  title: string;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Collapsible.Root className="border-b-[0.5px] border-line last:border-b-0">
      <Collapsible.Trigger className="group flex w-full items-center gap-3 rounded-md py-3 text-left text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
        <span aria-hidden className="text-muted-foreground">
          {icon}
        </span>
        <span className="flex-1 font-medium">{title}</span>
        <ChevronDown
          aria-hidden
          className="size-4 text-muted-foreground transition-transform group-data-panel-open:rotate-180"
        />
      </Collapsible.Trigger>
      <Collapsible.Panel className="pb-3 pl-7 text-sm text-muted-foreground">
        {children}
      </Collapsible.Panel>
    </Collapsible.Root>
  );
}
