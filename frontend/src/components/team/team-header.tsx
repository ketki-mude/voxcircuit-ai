"use client";

import { Menu } from "@base-ui/react/menu";
import { AudioWaveform, LogOut } from "lucide-react";
import Link from "next/link";

import { useTeamSession } from "@/hooks/use-team-session";
import type { TeamUser } from "@/lib/team-api";

export function TeamHeader() {
  const { user, signOut } = useTeamSession();
  return (
    <header className="border-b-[0.5px] border-line bg-card">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <div
            aria-hidden
            className="flex size-8 items-center justify-center rounded-lg bg-hero text-hero-foreground"
          >
            <AudioWaveform className="size-4" />
          </div>
          <div className="leading-tight">
            <p className="text-sm font-semibold">VoxCircuit</p>
            <p className="text-xs text-muted-foreground">Northstar Insurance, fictional demo</p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="rounded-md text-sm text-muted-foreground underline-offset-4 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            View as customer
          </Link>
          {user && <AccountMenu user={user} onSignOut={signOut} />}
        </div>
      </div>
    </header>
  );
}

function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return `${first}${last}`.toUpperCase();
}

function AccountMenu({ user, onSignOut }: { user: TeamUser; onSignOut: () => void }) {
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={`Account menu for ${user.name}`}
        className="flex size-8 items-center justify-center rounded-full border-[0.5px] border-line bg-muted text-xs font-semibold outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring/50 data-popup-open:bg-accent"
      >
        {initials(user.name)}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={8} align="end" className="z-50 outline-none">
          <Menu.Popup className="min-w-56 rounded-lg border-[0.5px] border-line bg-popover p-1 text-popover-foreground outline-none">
            <div className="flex flex-col gap-1 px-3 py-2">
              <p className="text-sm font-medium">{user.name}</p>
              <p className="text-xs text-muted-foreground">{user.email}</p>
              <p className="text-xs text-muted-foreground">
                Role: <span className="capitalize">{user.role}</span>
              </p>
            </div>
            <Menu.Separator className="my-1 h-[0.5px] bg-line" />
            <Menu.Item
              onClick={onSignOut}
              className="flex cursor-default items-center gap-2 rounded-md px-3 py-2 text-sm outline-none select-none data-highlighted:bg-accent"
            >
              <LogOut aria-hidden className="size-4" />
              Sign out
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
