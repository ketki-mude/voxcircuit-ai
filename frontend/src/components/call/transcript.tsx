"use client";

import { MessagesSquare } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";

import type { TranscriptEntry } from "@/hooks/use-call";
import { cn } from "@/lib/utils";

interface TranscriptProps {
  messages: TranscriptEntry[];
  /** Maya's reply as it streams in. */
  draft: string;
  /** The caller's speech as it is transcribed. */
  userDraft: string;
}

export function Transcript({ messages, draft, userDraft }: TranscriptProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length, draft, userDraft]);

  if (messages.length === 0 && !draft && !userDraft) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 py-10 text-center">
        <MessagesSquare aria-hidden className="size-8 text-muted-foreground/60" />
        <p className="text-sm font-medium">Your conversation will appear here</p>
        <p className="text-sm text-muted-foreground">Start a call, or type a message below.</p>
      </div>
    );
  }

  return (
    <>
      <ol aria-label="Call transcript" className="flex flex-col gap-3">
        {messages.map((entry) => (
          <Bubble key={entry.key} role={entry.role} interrupted={entry.interrupted}>
            {entry.text}
          </Bubble>
        ))}
        {userDraft && (
          <Bubble role="user" streaming>
            {userDraft}
          </Bubble>
        )}
        {draft && (
          <Bubble role="assistant" streaming>
            {draft}
          </Bubble>
        )}
      </ol>
      <div ref={endRef} />
    </>
  );
}

function Bubble({
  role,
  interrupted = false,
  streaming = false,
  children,
}: {
  role: "user" | "assistant";
  interrupted?: boolean;
  streaming?: boolean;
  children: ReactNode;
}) {
  const isUser = role === "user";
  return (
    <li
      className={cn(
        "flex flex-col gap-1 rounded-xl px-4 py-3",
        isUser ? "bg-accent" : "bg-muted",
        streaming && "opacity-80",
      )}
    >
      <span
        className={cn("text-xs font-medium", isUser ? "text-primary" : "text-muted-foreground")}
      >
        {isUser ? "You" : "Maya"}
      </span>
      <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground">{children}</p>
      {interrupted && <span className="text-xs text-muted-foreground">Interrupted</span>}
    </li>
  );
}
