import { AudioWaveform } from "lucide-react";
import type { ReactNode } from "react";

import { ViewToggle } from "@/components/view-toggle";

export function SiteHeader({ status }: { status?: ReactNode }) {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <div
            aria-hidden
            className="flex size-10 items-center justify-center rounded-xl bg-hero text-hero-foreground"
          >
            <AudioWaveform className="size-5" />
          </div>
          <div className="leading-tight">
            <p className="font-heading text-lg font-semibold">VoxCircuit</p>
            <p className="text-xs text-muted-foreground">Northstar Insurance, fictional demo</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {status}
          <ViewToggle />
        </div>
      </div>
    </header>
  );
}
