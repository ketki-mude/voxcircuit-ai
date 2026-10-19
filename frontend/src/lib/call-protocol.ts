// Messages exchanged over WS /ws/call. Mirrors backend/app/calls/protocol.py.
// JSON text frames carry the messages below. In a voice call, binary frames carry raw
// 16-bit little-endian mono PCM: caller audio at 16 kHz up, Maya's audio at 24 kHz down.

export const CONSENT_NOTICE =
  "This demo uses an AI voice and transcribes the call. Use fake details only.";
export const MAX_USER_TEXT_CHARS = 1000;

export type Engine = "gemini" | "fake";
export type InputMode = "text" | "audio";

export type EndReason =
  | "caller_ended"
  | "disconnected"
  | "max_duration"
  | "max_turns"
  | "daily_cap"
  | "quota"
  | "error";

// Client -> server

export type ClientMessage =
  | { type: "start"; engine: Engine; input_mode: InputMode; consent: boolean }
  | { type: "user_text"; text: string }
  | { type: "end" };

// Server -> client

export interface CallStarted {
  type: "call_started";
  call_id: string;
  engine: Engine;
  max_call_seconds: number;
  max_turns: number;
}

export interface UserDelta {
  type: "user_delta";
  text: string;
}

export interface AssistantDelta {
  type: "assistant_delta";
  text: string;
}

export interface MessageSaved {
  type: "message";
  id: number;
  seq: number;
  role: "user" | "assistant";
  text: string;
  interrupted: boolean;
  latency_ms: number | null;
}

export interface InterruptedNotice {
  type: "interrupted";
}

/** Caller's last input (voiced audio or typed turn) to Maya's first audio, in ms. */
export interface LatencyMeasured {
  type: "latency";
  latency_ms: number;
}

export interface TurnCompleted {
  type: "turn_complete";
  latency_ms: number | null;
}

export interface UsageUpdate {
  type: "usage";
  elapsed_seconds: number;
  user_turns: number;
  audio_seconds_in: number;
  audio_seconds_out: number;
  estimated_cost_usd: number;
}

export interface CallEnded {
  type: "call_ended";
  reason: EndReason;
  message: string;
}

export interface ErrorNotice {
  type: "error";
  code: "invalid_message" | "consent_required" | "not_configured";
  message: string;
}

export type ServerMessage =
  | CallStarted
  | UserDelta
  | AssistantDelta
  | LatencyMeasured
  | MessageSaved
  | InterruptedNotice
  | TurnCompleted
  | UsageUpdate
  | CallEnded
  | ErrorNotice;

const SERVER_TYPES = new Set<ServerMessage["type"]>([
  "call_started",
  "user_delta",
  "assistant_delta",
  "latency",
  "message",
  "interrupted",
  "turn_complete",
  "usage",
  "call_ended",
  "error",
]);

export function parseServerMessage(data: unknown): ServerMessage | null {
  if (typeof data !== "string") return null;
  try {
    const parsed: unknown = JSON.parse(data);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "type" in parsed &&
      SERVER_TYPES.has((parsed as { type: ServerMessage["type"] }).type)
    ) {
      return parsed as ServerMessage;
    }
  } catch {
    // Ignore malformed frames.
  }
  return null;
}
