"use client";

import {
  ArrowUp,
  Bot,
  CircleCheck,
  Headphones,
  Info,
  Keyboard,
  LoaderCircle,
  MessageSquareText,
  Mic,
  PhoneOff,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";

import { ApprovalTicket } from "@/components/call/approval-ticket";
import { CircuitTimeline } from "@/components/call/circuit-timeline";
import { ConsentBanner } from "@/components/call/consent-banner";
import { type MayaStatus, StatusPill } from "@/components/call/status-pill";
import { Transcript } from "@/components/call/transcript";
import { VerificationCard } from "@/components/call/verification-card";
import { type WaveSource, Waveform } from "@/components/call/waveform";
import { WelcomeHero } from "@/components/call/welcome-hero";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { type CallState, isSpeaking, useCall } from "@/hooks/use-call";
import { type Engine, MAX_USER_TEXT_CHARS } from "@/lib/call-protocol";
import { cn } from "@/lib/utils";

/** Wall-clock time, ticking once a second while running. 0 until the first tick. */
function useNow(running: boolean): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);
  return now;
}

function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function mayaStatus(state: CallState): MayaStatus {
  if (state.phase === "connecting") return "Connecting";
  if (state.ticket) return "Needs your OK";
  if (state.outcome) return state.outcome.booked ? "Booked" : "Not booked";
  if (state.phase === "active") return isSpeaking(state) ? "Speaking" : "Listening";
  return "Ready";
}

/** The large line under the waveform, and the sentence under it. */
function headline(state: CallState, consented: boolean): [string, string] {
  switch (state.phase) {
    case "connecting":
      return ["Connecting to Maya", "This takes a moment."];
    case "ended":
      return ["Call ended", "Start a new call whenever you're ready."];
    case "active":
      if (state.ticket) return ["Your OK is needed", "Nothing is booked until you press Approve."];
      if (state.inputMode === "text") {
        return isSpeaking(state)
          ? ["Maya is replying", "Her answer appears in the conversation."]
          : ["Chat in progress", "Type your message in the conversation."];
      }
      return isSpeaking(state)
        ? ["Maya is speaking", "Talk any time to interrupt her."]
        : ["Listening", "Go ahead, Maya is listening."];
    case "idle":
      return [
        "Ready when you are",
        consented
          ? "Start a voice call, or type a message to chat."
          : "Tick “I understand” above, then start a call.",
      ];
  }
}

function waveSource(state: CallState): WaveSource {
  if (state.phase !== "active" || state.inputMode !== "audio") return "idle";
  return state.playing ? "maya" : "mic";
}

export function CallConsole() {
  const {
    state,
    start,
    sendText,
    end,
    decide,
    openVerification,
    closeVerification,
    startVerification,
    checkCode,
    deleteMyData,
    getAnalysers,
  } = useCall();
  const [engine, setEngine] = useState<Engine>("gemini");
  const [consented, setConsented] = useState(false);
  const [text, setText] = useState("");
  // A message typed before a call: it starts a text chat and is sent once Maya is connected.
  const pendingRef = useRef<string | null>(null);
  const now = useNow(state.phase === "active");

  useEffect(() => {
    if (state.phase === "active" && pendingRef.current) {
      sendText(pendingRef.current);
      pendingRef.current = null;
    } else if (state.phase === "idle" || state.phase === "ended") {
      pendingRef.current = null;
    }
  }, [state.phase, sendText]);

  const inCall = state.phase === "connecting" || state.phase === "active";
  const started = state.phase !== "idle" || state.messages.length > 0;
  // The offline engine only answers typed turns.
  const voiceAvailable = engine === "gemini";
  const elapsed =
    state.phase === "active" && state.startedAt !== null
      ? Math.max(0, now - state.startedAt) / 1000
      : (state.usage?.elapsed_seconds ?? 0);
  const [title, detail] = headline(state, consented);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = text.trim();
    if (!message) return;
    if (state.phase === "active") {
      sendText(message);
    } else if (!inCall && consented) {
      pendingRef.current = message;
      void start(engine, "text");
    } else {
      return;
    }
    setText("");
  }

  const composerDisabled = state.phase === "connecting" || (!inCall && !consented);
  const placeholder =
    state.phase === "connecting"
      ? "Connecting…"
      : state.phase === "active"
        ? state.inputMode === "audio"
          ? "Speak, or type a message…"
          : "Type your message…"
        : consented
          ? "Or type a message…"
          : "Tick “I understand” above to start";

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-5 px-4 py-6">
      <WelcomeHero />
      <ConsentBanner checked={consented} onCheckedChange={setConsented} disabled={inCall} />

      <div className="grid gap-5 lg:grid-cols-2 lg:items-start">
        <div className="flex min-w-0 flex-col gap-5">
          {state.ticket && (
            <ApprovalTicket
              ticket={state.ticket}
              onDecide={(approvalId, decision) => void decide(approvalId, decision)}
            />
          )}

          {state.verification && (
            <VerificationCard
              verification={state.verification}
              onSendCode={(email) => void startVerification(email)}
              onCheckCode={(code) => void checkCode(code)}
              onDeleteData={() => void deleteMyData()}
              onClose={closeVerification}
            />
          )}

          <Card>
            <CardHeader>
              <div className="flex items-center gap-3">
                <div
                  aria-hidden
                  className="flex size-11 items-center justify-center rounded-full bg-accent text-primary"
                >
                  <Bot className="size-5" />
                </div>
                <div>
                  <CardTitle className="text-lg">Maya</CardTitle>
                  <CardDescription>Your AI assistant</CardDescription>
                </div>
              </div>
              <CardAction>
                <StatusPill status={mayaStatus(state)} />
              </CardAction>
            </CardHeader>

            <CardContent className="flex flex-col items-center gap-5 pb-2 text-center">
              <Waveform getAnalysers={getAnalysers} source={waveSource(state)} />

              <div className="flex flex-col gap-1">
                <p className="font-heading text-xl font-semibold">{title}</p>
                <p className="text-sm text-muted-foreground">{detail}</p>
              </div>

              {state.phase !== "idle" && (
                <dl className="grid w-full max-w-sm grid-cols-3 divide-x rounded-lg bg-muted/60 py-2">
                  <Stat
                    label="Latency"
                    title="From your last words (or message) to Maya's first audio."
                    value={state.lastLatencyMs !== null ? `${state.lastLatencyMs} ms` : "--"}
                  />
                  <Stat
                    label="Time"
                    value={
                      state.limits
                        ? `${formatDuration(elapsed)} / ${formatDuration(state.limits.maxCallSeconds)}`
                        : "--"
                    }
                  />
                  <Stat
                    label="Turns"
                    value={
                      state.limits
                        ? `${state.usage?.user_turns ?? 0} / ${state.limits.maxTurns}`
                        : "--"
                    }
                  />
                </dl>
              )}

              {inCall ? (
                <RoundButton
                  label={state.phase === "connecting" ? "Connecting" : "End call"}
                  tone="end"
                  disabled={state.phase === "connecting"}
                  onClick={end}
                >
                  {state.phase === "connecting" ? (
                    <LoaderCircle className="size-7 motion-safe:animate-spin" />
                  ) : (
                    <PhoneOff className="size-7" />
                  )}
                </RoundButton>
              ) : (
                <RoundButton
                  label="Start call"
                  tone="start"
                  disabled={!consented}
                  onClick={() => void start(engine, voiceAvailable ? "audio" : "text")}
                >
                  {voiceAvailable ? (
                    <Mic className="size-7" />
                  ) : (
                    <MessageSquareText className="size-7" />
                  )}
                </RoundButton>
              )}

              {(inCall ? state.inputMode === "audio" : voiceAvailable) && (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Headphones aria-hidden className="size-4 shrink-0" />
                  Use headphones so Maya doesn&rsquo;t hear herself.
                </p>
              )}

              {state.phase === "active" && !state.verification && (
                <Button variant="link" onClick={openVerification}>
                  I&rsquo;ve called before
                </Button>
              )}
            </CardContent>

            <CardFooter className="flex-wrap justify-between gap-x-4 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">Model</span>
                <select
                  value={engine}
                  onChange={(event) => setEngine(event.target.value as Engine)}
                  disabled={inCall}
                  className="h-8 rounded-lg border border-input bg-background px-2 text-sm font-medium outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <option value="gemini">Gemini Live</option>
                  <option value="fake">Offline test</option>
                </select>
              </label>
              <span className="text-xs text-muted-foreground">
                {voiceAvailable
                  ? "Talk with the real model."
                  : "Scripted text replies, no network."}
              </span>
            </CardFooter>
          </Card>

          {state.handoff && (
            <Alert className="border-warning/50">
              <Info aria-hidden className="text-warning" />
              <AlertDescription className="text-foreground">
                A licensed agent has been asked to take over ({state.handoff.label.toLowerCase()}).
                They will follow up with you.
              </AlertDescription>
            </Alert>
          )}

          {state.notice && (
            <Alert variant={state.notice.tone === "error" ? "destructive" : "default"}>
              {state.notice.tone === "error" && <TriangleAlert aria-hidden />}
              <AlertDescription
                className={state.notice.tone === "error" ? undefined : "text-foreground"}
              >
                {state.notice.text}
              </AlertDescription>
            </Alert>
          )}

          {started && (
            <Card>
              <CardHeader>
                <CardTitle>What Maya is doing</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <CircuitTimeline circuit={state.circuit} lastAction={state.lastAction} />
                {state.outcome && (
                  <Alert
                    className={cn(
                      state.outcome.booked
                        ? "border-success/40 text-success"
                        : "border-destructive/40 text-destructive",
                    )}
                  >
                    {state.outcome.booked ? (
                      <CircleCheck aria-hidden />
                    ) : (
                      <TriangleAlert aria-hidden />
                    )}
                    <AlertDescription className="text-foreground">
                      {state.outcome.summary}
                    </AlertDescription>
                  </Alert>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <Card className="h-[34rem] lg:sticky lg:top-6 lg:h-[38rem]">
          <CardHeader className="border-b">
            <CardTitle className="flex items-center gap-2 text-lg">
              <MessageSquareText aria-hidden className="size-5 text-muted-foreground" />
              Conversation
            </CardTitle>
            {state.phase !== "idle" && (
              <CardAction>
                <Badge variant="secondary">
                  {state.inputMode === "audio" ? "Voice call" : "Text chat"}
                </Badge>
              </CardAction>
            )}
          </CardHeader>
          <CardContent className="min-h-0 flex-1 overflow-y-auto">
            <Transcript messages={state.messages} draft={state.draft} userDraft={state.userDraft} />
          </CardContent>
          <form onSubmit={onSubmit} className="px-4">
            <div className="flex items-center gap-3 rounded-xl border border-input bg-background py-2 pr-2 pl-3 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50 has-disabled:opacity-60">
              <Keyboard aria-hidden className="size-5 shrink-0 text-muted-foreground" />
              <input
                aria-label="Message to Maya"
                placeholder={placeholder}
                value={text}
                onChange={(event) => setText(event.target.value)}
                maxLength={MAX_USER_TEXT_CHARS}
                disabled={composerDisabled}
                autoComplete="off"
                className="min-w-0 flex-1 bg-transparent py-1 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
              />
              <Button
                type="submit"
                size="icon"
                aria-label="Send"
                disabled={composerDisabled || !text.trim()}
                className="rounded-full"
              >
                <ArrowUp />
              </Button>
            </div>
          </form>
        </Card>
      </div>

      <p className="flex items-center justify-center gap-2 text-center text-sm text-muted-foreground">
        <ShieldCheck aria-hidden className="size-4 shrink-0" />
        Nothing is booked without your approval. Calls are transcribed as text; audio is never
        stored.
      </p>
    </main>
  );
}

function RoundButton({
  label,
  tone,
  disabled,
  onClick,
  children,
}: {
  label: string;
  tone: "start" | "end";
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="group flex flex-col items-center gap-2 rounded-2xl p-1 outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed"
    >
      <span
        aria-hidden
        className={cn(
          "flex size-16 items-center justify-center rounded-full shadow-sm transition-colors",
          tone === "start"
            ? "bg-primary text-primary-foreground group-hover:bg-primary/90"
            : "bg-destructive text-white group-hover:bg-destructive/90",
          "group-disabled:bg-muted group-disabled:text-muted-foreground group-disabled:shadow-none",
        )}
      >
        {children}
      </span>
      <span
        className={cn(
          "text-sm font-semibold group-disabled:text-muted-foreground",
          tone === "start" ? "text-primary" : "text-destructive",
        )}
      >
        {label}
      </span>
    </button>
  );
}

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="px-2" title={title}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-mono text-sm tabular-nums">{value}</dd>
    </div>
  );
}
