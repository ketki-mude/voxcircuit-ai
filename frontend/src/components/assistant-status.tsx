"use client";

import { useEffect, useState } from "react";

import { BACKEND_HTTP_URL } from "@/lib/config";
import { cn } from "@/lib/utils";

type Health = "checking" | "online" | "offline";

const LABEL: Record<Health, string> = {
  checking: "Checking assistant",
  online: "Assistant online",
  offline: "Assistant offline",
};

/** Whether the backend answers its health check, rechecked every 30 seconds. */
export function AssistantStatus() {
  const [health, setHealth] = useState<Health>("checking");

  useEffect(() => {
    let cancelled = false;
    const check = () =>
      fetch(`${BACKEND_HTTP_URL}/api/health`, { cache: "no-store" })
        .then((response) => (response.ok ? "online" : "offline"))
        .catch((): Health => "offline")
        .then((next) => {
          if (!cancelled) setHealth(next);
        });
    void check();
    const id = setInterval(() => void check(), 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <span
      role="status"
      className={cn(
        "inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium whitespace-nowrap",
        health === "online" && "bg-success/10 text-success",
        health === "offline" && "bg-destructive/10 text-destructive",
        health === "checking" && "bg-muted text-muted-foreground",
      )}
    >
      <span aria-hidden className="size-2.5 rounded-full bg-current" />
      {LABEL[health]}
    </span>
  );
}
