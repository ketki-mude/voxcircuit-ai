"use client";

import {
  CircleCheck,
  Headphones,
  Info,
  LoaderCircle,
  Phone,
  PhoneOff,
  SendHorizontal,
  TriangleAlert,
} from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import { ApprovalTicket } from "@/components/call/approval-ticket";
import { CircuitTimeline } from "@/components/call/circuit-timeline";
import { ConsentBanner } from "@/components/call/consent-banner";
import { type MayaStatus, StatusPill } from "@/components/call/status-pill";
import { Transcript } from "@/components/call/transcript";
import { VerificationCard } from "@/components/call/verification-card";
import { Waveform } from "@/components/call/waveform";
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
import { Input } from "@/components/ui/input";
import { type CallState, isSpeaking, useCall } from "@/hooks/use-call";
import { type Engine, type InputMode, MAX_USER_TEXT_CHARS } from "@/lib/call-protocol";
import { cn } from "@/lib/utils";

interface Option<T extends string> {
  value: T;
  label: string;
  hint: string;
  disabled?: boolean;
}

const ENGINES: Option<Engine>[] = [
  { value: "gemini", label: "Gemini Live", hint: "Talk with the real model." },
  { value: "fake", label: "Offline test", hint: "Scripted replies, no network." },
];

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
  const [mode, setMode] = useState<InputMode>("audio");
  const [consented, setConsented] = useState(false);
  const [text, setText] = useState("");
  const now = useNow(state.phase === "active");

  // The offline engine only answers typed turns.
  const selectedMode: InputMode = engine === "fake" ? "text" : mode;
  const modes: Option<InputMode>[] = [
    {
      value: "audio",
      label: "Voice",
      hint: engine === "fake" ? "Needs Gemini Live." : "Talk through your microphone.",
      disabled: engine === "fake",
    },
    { value: "text", label: "Text", hint: "Type your replies." },
  ];

  const inCall = state.phase === "connecting" || state.phase === "active";
  const voiceCall = inCall && state.inputMode === "audio";
  const elapsed =
    state.phase === "active" && state.startedAt !== null
      ? Math.max(0, now - state.startedAt) / 1000
      : (state.usage?.elapsed_seconds ?? 0);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = text.trim();
    if (!message) return;
    sendText(message);
    setText("");
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
      <ConsentBanner checked={consented} onCheckedChange={setConsented} disabled={inCall} />

      <Card>
        <CardHeader>
          <div className="flex items-center gap-3">
            <div
              aria-hidden
              className="flex size-10 items-center justify-center rounded-full bg-primary/10 font-heading font-semibold text-primary"
            >
              M
            </div>
            <div>
              <CardTitle>Maya</CardTitle>
              <CardDescription>AI assistant for Northstar Insurance</CardDescription>
            </div>
          </div>
          <CardAction>
            <StatusPill status={mayaStatus(state)} />
          </CardAction>
        </CardHeader>

        <CardContent className="flex flex-col gap-5">
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
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
                state.limits ? `${state.usage?.user_turns ?? 0} / ${state.limits.maxTurns}` : "--"
              }
            />
            <Stat
              label="Est. cost"
              title="Estimated at paid-tier audio rates. The free tier is not billed."
              value={state.usage ? `$${state.usage.estimated_cost_usd.toFixed(4)}` : "--"}
            />
          </dl>

          {voiceCall && <Waveform getAnalysers={getAnalysers} speaking={state.playing} />}

          {inCall ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <Button
                variant="outline"
                size="lg"
                onClick={end}
                disabled={state.phase === "connecting"}
                className="self-start"
              >
                {state.phase === "connecting" ? (
                  <LoaderCircle className="motion-safe:animate-spin" />
                ) : (
                  <PhoneOff />
                )}
                {state.phase === "connecting" ? "Connecting" : "End call"}
              </Button>
              {state.inputMode === "audio" && <HeadphonesTip />}
              {state.phase === "active" && !state.verification && (
                <Button variant="link" className="sm:ml-auto" onClick={openVerification}>
                  I&rsquo;ve called before
                </Button>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <OptionGroup
                legend="Model"
                name="engine"
                options={ENGINES}
                value={engine}
                onChange={setEngine}
              />
              <OptionGroup
                legend="Mode"
                name="mode"
                options={modes}
                value={selectedMode}
                onChange={setMode}
              />
              {selectedMode === "audio" && <HeadphonesTip />}
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <Button
                  size="lg"
                  onClick={() => void start(engine, selectedMode)}
                  disabled={!consented}
                >
                  <Phone />
                  Start call
                </Button>
                {!consented && (
                  <p className="text-sm text-muted-foreground">
                    Tick &ldquo;I understand&rdquo; above to start.
                  </p>
                )}
              </div>
            </div>
          )}
        </CardContent>
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

      {state.verification && (
        <VerificationCard
          verification={state.verification}
          onSendCode={(email) => void startVerification(email)}
          onCheckCode={(code) => void checkCode(code)}
          onDeleteData={() => void deleteMyData()}
          onClose={closeVerification}
        />
      )}

      {state.ticket && (
        <ApprovalTicket
          ticket={state.ticket}
          onDecide={(approvalId, decision) => void decide(approvalId, decision)}
        />
      )}

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
              {state.outcome.booked ? <CircleCheck aria-hidden /> : <TriangleAlert aria-hidden />}
              <AlertDescription className="text-foreground">{state.outcome.summary}</AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

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

      <Card>
        <CardHeader>
          <CardTitle>Transcript</CardTitle>
          <CardAction>
            <Badge variant="secondary">
              {state.inputMode === "audio" && state.phase !== "idle" ? "Voice" : "Text"} mode
            </Badge>
          </CardAction>
        </CardHeader>
        <CardContent>
          <div className="max-h-[50vh] min-h-48 overflow-y-auto pr-1">
            <Transcript
              messages={state.messages}
              draft={state.draft}
              userDraft={state.userDraft}
            />
          </div>
        </CardContent>
        <CardFooter>
          <form onSubmit={onSubmit} className="flex w-full gap-2">
            <Input
              aria-label="Message to Maya"
              placeholder={
                state.phase !== "active"
                  ? "Start a call to chat"
                  : state.inputMode === "audio"
                    ? "Speak, or type a message"
                    : "Type your reply"
              }
              value={text}
              onChange={(event) => setText(event.target.value)}
              maxLength={MAX_USER_TEXT_CHARS}
              disabled={state.phase !== "active"}
              autoComplete="off"
            />
            <Button type="submit" disabled={state.phase !== "active" || !text.trim()}>
              <SendHorizontal />
              Send
            </Button>
          </form>
        </CardFooter>
      </Card>
    </main>
  );
}

function HeadphonesTip() {
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <Headphones aria-hidden className="size-4 shrink-0" />
      Use headphones so Maya doesn&rsquo;t hear herself.
    </p>
  );
}

function OptionGroup<T extends string>({
  legend,
  name,
  options,
  value,
  onChange,
}: {
  legend: string;
  name: string;
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-sm font-medium">{legend}</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <label
            key={option.value}
            className={cn(
              "flex flex-col rounded-lg border px-3 py-2 text-sm transition-colors has-focus-visible:ring-3 has-focus-visible:ring-ring/50",
              option.disabled
                ? "cursor-not-allowed opacity-50"
                : value === option.value
                  ? "cursor-pointer border-primary bg-primary/5"
                  : "cursor-pointer hover:bg-muted",
            )}
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              disabled={option.disabled}
              onChange={() => onChange(option.value)}
              className="sr-only"
            />
            <span className="font-medium">{option.label}</span>
            <span className="text-muted-foreground">{option.hint}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="rounded-lg bg-muted px-3 py-2" title={title}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-mono text-sm tabular-nums">{value}</dd>
    </div>
  );
}
