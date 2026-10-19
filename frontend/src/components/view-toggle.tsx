"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense } from "react";

import { cn } from "@/lib/utils";

const VIEWS = [
  { href: "/", label: "Customer view", team: false },
  { href: "/control-room", label: "Team control room", team: true },
];

export function ViewToggle() {
  // The current path is only known at request time on dynamic routes, so it streams in.
  return (
    <Suspense fallback={<Links team={null} />}>
      <CurrentView />
    </Suspense>
  );
}

function CurrentView() {
  return <Links team={usePathname().startsWith("/control-room")} />;
}

function Links({ team }: { team: boolean | null }) {
  return (
    <nav aria-label="View" className="flex rounded-lg border bg-muted p-0.5 text-sm">
      {VIEWS.map((view) => {
        const active = view.team === team;
        return (
          <Link
            key={view.href}
            href={view.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "rounded-md px-3 py-1 whitespace-nowrap transition-colors",
              active ? "bg-card font-medium shadow-sm" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {view.label}
          </Link>
        );
      })}
    </nav>
  );
}
