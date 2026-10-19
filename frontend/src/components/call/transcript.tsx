"use client";

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
      <p className="py-10 text-center text-sm text-muted-foreground">
        The conversation will appear here.
      </p>
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
    <li className={cn("flex flex-col gap-1", isUser ? "items-end" : "items-start")}>
      <span className="text-xs text-muted-foreground">{isUser ? "You" : "Maya"}</span>
      <p
        className={cn(
          "max-w-[85%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed whitespace-pre-wrap",
          isUser
            ? "rounded-br-sm bg-primary text-primary-foreground"
            : "rounded-bl-sm bg-muted text-foreground",
          streaming && "opacity-80",
        )}
      >
        {children}
      </p>
      {interrupted && <span className="text-xs text-muted-foreground">Interrupted</span>}
    </li>
  );
}
