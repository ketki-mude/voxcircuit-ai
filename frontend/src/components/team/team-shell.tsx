"use client";

import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type FormEvent, type ReactNode, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useTeamSession } from "@/hooks/use-team-session";
import { cn } from "@/lib/utils";

const SECTIONS = [
  { href: "/control-room", label: "Overview" },
  { href: "/control-room/calls", label: "Calls" },
  { href: "/control-room/evaluation", label: "Evaluation" },
];

/** Signs the team member in, then shows the control-room sections. */
export function TeamShell({ children }: { children: ReactNode }) {
  const { ready, user } = useTeamSession();
  const pathname = usePathname();

  if (!ready) {
    return <main className="flex-1" aria-busy />;
  }
  if (!user) {
    return <SignIn />;
  }
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-4 pb-6">
      <nav
        aria-label="Control room"
        className="-mx-4 flex gap-4 overflow-x-auto border-b-[0.5px] border-line px-4 text-sm"
      >
        {SECTIONS.map((section) => {
          const active =
            section.href === "/control-room"
              ? pathname === section.href
              : pathname.startsWith(section.href);
          return (
            <Link
              key={section.href}
              href={section.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "-mb-[0.5px] border-b-2 py-3 whitespace-nowrap outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                active
                  ? "border-primary font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {section.label}
            </Link>
          );
        })}
      </nav>
      <main className="flex flex-col gap-6">{children}</main>
    </div>
  );
}

function SignIn() {
  const { signIn } = useTeamSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center px-4 py-10">
      <Card>
        <CardHeader>
          <CardTitle>Team control room</CardTitle>
          <CardDescription>Sign in with your Northstar team account.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1.5 text-sm font-medium">
              Email
              <Input
                type="email"
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm font-medium">
              Password
              <Input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" disabled={busy}>
              {busy && <LoaderCircle className="motion-safe:animate-spin" />}
              Sign in
            </Button>
            <p className="text-xs text-muted-foreground">
              Need access? Ask your team administrator.
            </p>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
