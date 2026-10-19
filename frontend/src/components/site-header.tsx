import { AudioWaveform } from "lucide-react";

import { ViewToggle } from "@/components/view-toggle";

export function SiteHeader() {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <div
            aria-hidden
            className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground"
          >
            <AudioWaveform className="size-5" />
          </div>
          <div className="leading-tight">
            <p className="font-heading text-base font-semibold">VoxCircuit</p>
            <p className="text-xs text-muted-foreground">Northstar Insurance, fictional demo</p>
          </div>
        </div>
        <ViewToggle />
      </div>
    </header>
  );
}
