"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";

import { MicCapture, describeMicError } from "@/lib/audio/mic-capture";
import { PcmPlayer } from "@/lib/audio/pcm-player";
import {
  type ClientMessage,
  type Engine,
  type EndReason,
  type InputMode,
  type ServerMessage,
  type UsageUpdate,
  parseServerMessage,
} from "@/lib/call-protocol";
import { BACKEND_HTTP_URL, BACKEND_WS_URL } from "@/lib/config";

export type CallPhase = "idle" | "connecting" | "active" | "ended";

export interface TranscriptEntry {
  key: string;
  role: "user" | "assistant";
  text: string;
  interrupted: boolean;
}

export interface Notice {
  tone: "info" | "error";
  text: string;
}

export type StepStatus = "idle" | "active" | "done" | "failed" | "skipped";

/** "What Maya is doing": each step lights up from tool and approval events. */
export interface Circuit {
  heard: StepStatus;
  understood: StepStatus;
  calendar: StepStatus;
  approval: StepStatus;
  booked: StepStatus;
}

export interface Ticket {
  approvalId: string;
  action: "book" | "reschedule";
  rows: { label: string; value: string }[];
  expiresAt: string;
  /** The caller pressed a button and the answer is on its way. */
  submitting: boolean;
}

export interface BookingOutcome {
  booked: boolean;
  summary: string;
}

export interface Verification {
  stage: "email" | "code" | "verified" | "failed" | "expired";
  maskedEmail: string | null;
  /** Demo only: shown as a fake text message instead of texting the phone on file. */
  demoCode: string | null;
  expiresAt: string | null;
  attemptsLeft: number;
  returningCustomer: boolean;
  message: string | null;
  submitting: boolean;
}

export interface HandoffInfo {
  reason: string;
  label: string;
}

export interface CallState {
  phase: CallPhase;
  inputMode: InputMode;
  callId: string | null;
  limits: { maxCallSeconds: number; maxTurns: number } | null;
  startedAt: number | null;
  messages: TranscriptEntry[];
  /** Maya's reply as it is transcribed, before the backend saves it. */
  draft: string;
  /** The caller's speech as it is transcribed, before the backend saves it. */
  userDraft: string;
  /** Text call: Maya's reply is streaming in. */
  streaming: boolean;
  /** Voice call: Maya's audio is playing. */
  playing: boolean;
  lastLatencyMs: number | null;
  usage: UsageUpdate | null;
  notice: Notice | null;
  circuit: Circuit;
  /** Summary of the latest tool Maya ran, without personal details. */
  lastAction: string | null;
  ticket: Ticket | null;
  /** The latest booking result. Shown in the status pill until the caller speaks again. */
  outcome: BookingOutcome | null;
  verification: Verification | null;
  handoff: HandoffInfo | null;
}

export interface Analysers {
  mic: AnalyserNode | null;
  player: AnalyserNode | null;
}

type Action =
  | { type: "connect"; inputMode: InputMode }
  | { type: "server"; message: ServerMessage; at: number }
  | { type: "playback"; playing: boolean }
  | { type: "failed"; text: string }
  | { type: "deciding" }
  | { type: "request_failed"; text: string }
  | { type: "verification_opened" }
  | { type: "verification_closed" }
  | { type: "verification_sending" }
  | { type: "verification_problem"; text: string }
  | { type: "socket_closed" };

const FAILED_REASONS: ReadonlySet<EndReason> = new Set(["quota", "error", "daily_cap"]);

// Stop sending microphone frames if this much is already waiting on the socket.
const MAX_SOCKET_BACKLOG_BYTES = 256 * 1024;

const initialState: CallState = {
  phase: "idle",
  inputMode: "audio",
  callId: null,
  limits: null,
  startedAt: null,
  messages: [],
  draft: "",
  userDraft: "",
  streaming: false,
  playing: false,
  lastLatencyMs: null,
  usage: null,
  notice: null,
  circuit: {
    heard: "idle",
    understood: "idle",
    calendar: "idle",
    approval: "idle",
    booked: "idle",
  },
  lastAction: null,
  ticket: null,
  outcome: null,
  verification: null,
  handoff: null,
};

const EMPTY_VERIFICATION: Verification = {
  stage: "email",
  maskedEmail: null,
  demoCode: null,
  expiresAt: null,
  attemptsLeft: 3,
  returningCustomer: false,
  message: null,
  submitting: false,
};

function onVerificationResult(
  verification: Verification | null,
  message: Extract<ServerMessage, { type: "verification_result" }>,
): Verification | null {
  if (!verification || message.status === "no_challenge") return verification;
  const settled = { ...verification, submitting: false };
  switch (message.status) {
    case "verified":
      return {
        ...settled,
        stage: "verified",
        returningCustomer: message.returning_customer,
        message: null,
      };
    case "wrong_code": {
      const left = message.attempts_left;
      return {
        ...settled,
        attemptsLeft: left,
        message: `That code didn't match. ${left} ${left === 1 ? "try" : "tries"} left.`,
      };
    }
    case "failed":
      return {
        ...settled,
        stage: "failed",
        attemptsLeft: 0,
        message: "Verification failed. A licensed agent will follow up with you.",
      };
    case "expired":
      return { ...settled, stage: "expired", message: "That code expired." };
  }
}

const TOOL_STEPS: Record<string, keyof Circuit> = {
  create_or_update_lead: "understood",
  check_availability: "calendar",
};

/** Maya is talking: her audio is playing (voice) or her reply is streaming in (text). */
export function isSpeaking(state: CallState): boolean {
  return state.inputMode === "audio" ? state.playing : state.streaming;
}

function onServerMessage(state: CallState, message: ServerMessage, at: number): CallState {
  switch (message.type) {
    case "call_started":
      return {
        ...state,
        phase: "active",
        callId: message.call_id,
        limits: { maxCallSeconds: message.max_call_seconds, maxTurns: message.max_turns },
        startedAt: at,
      };
    case "user_delta":
      return { ...state, userDraft: state.userDraft + message.text };
    case "assistant_delta":
      return { ...state, draft: state.draft + message.text, streaming: true };
    case "latency":
      return { ...state, lastLatencyMs: message.latency_ms };
    case "message":
      return {
        ...state,
        circuit:
          message.role === "user" ? { ...state.circuit, heard: "done" } : state.circuit,
        outcome: message.role === "user" ? null : state.outcome,
        messages: [
          ...state.messages,
          {
            key: `${message.seq}`,
            role: message.role,
            text: message.text,
            interrupted: message.interrupted,
          },
        ],
        draft: message.role === "assistant" ? "" : state.draft,
        userDraft: message.role === "user" ? "" : state.userDraft,
      };
    case "interrupted":
      return { ...state, draft: "", streaming: false };
    case "turn_complete":
      return {
        ...state,
        streaming: false,
        lastLatencyMs: message.latency_ms ?? state.lastLatencyMs,
      };
    case "usage":
      return { ...state, usage: message };
    case "tool": {
      const step = TOOL_STEPS[message.name];
      return {
        ...state,
        lastAction: message.summary,
        circuit:
          step && message.ok ? { ...state.circuit, [step]: "done" } : state.circuit,
      };
    }
    case "approval_requested":
      return {
        ...state,
        outcome: null,
        circuit: { ...state.circuit, approval: "active", booked: "idle" },
        ticket: {
          approvalId: message.approval_id,
          action: message.action,
          rows: message.rows,
          expiresAt: message.expires_at,
          submitting: false,
        },
      };
    case "approval_resolved": {
      const ticket = state.ticket;
      if (!ticket || ticket.approvalId !== message.approval_id) return state;
      if (message.status === "approved") {
        return {
          ...state,
          circuit: { ...state.circuit, approval: "done", booked: "active" },
          ticket: { ...ticket, submitting: true },
        };
      }
      // Replaced by a newer ticket: nothing to report. Cancelled by the caller, or expired:
      // nothing was booked.
      const replaced = message.status === "cancelled" && !ticket.submitting;
      return {
        ...state,
        ticket: null,
        lastAction: replaced ? state.lastAction : "Nothing was booked",
        circuit: replaced
          ? state.circuit
          : { ...state.circuit, approval: "skipped", booked: "skipped" },
        outcome: replaced
          ? state.outcome
          : { booked: false, summary: "Nothing was booked." },
      };
    }
    case "verification_started":
      return {
        ...state,
        verification: {
          ...EMPTY_VERIFICATION,
          stage: "code",
          maskedEmail: message.masked_email,
          demoCode: message.demo_code,
          expiresAt: message.expires_at,
        },
      };
    case "verification_result":
      return { ...state, verification: onVerificationResult(state.verification, message) };
    case "handoff":
      return { ...state, handoff: { reason: message.reason, label: message.label } };
    case "handoff_taken":
      return {
        ...state,
        notice: {
          tone: "info",
          text: `${message.agent_name}, a licensed agent, has taken over your call.`,
        },
      };
    case "data_deleted":
      return {
        ...state,
        verification: null,
        notice: { tone: "info", text: "Your stored data was deleted." },
      };
    case "booking_result":
      return {
        ...state,
        ticket: null,
        circuit: {
          ...state.circuit,
          approval: "done",
          booked: message.success ? "done" : "failed",
        },
        lastAction: message.success ? "Booked and saved to CRM" : "Booking not completed",
        outcome: { booked: message.success, summary: message.summary },
      };
    case "call_ended":
      return {
        ...state,
        phase: "ended",
        draft: "",
        userDraft: "",
        streaming: false,
        playing: false,
        ticket: null,
        verification: null,
        notice: {
          tone: FAILED_REASONS.has(message.reason) ? "error" : "info",
          text: message.message,
        },
      };
    case "error":
      // Before the call starts, an error means the call could not start.
      return {
        ...state,
        phase: state.phase === "connecting" ? "idle" : state.phase,
        notice: { tone: "error", text: message.message },
      };
  }
}

function reducer(state: CallState, action: Action): CallState {
  switch (action.type) {
    case "connect":
      return { ...initialState, phase: "connecting", inputMode: action.inputMode };
    case "server":
      return onServerMessage(state, action.message, action.at);
    case "playback":
      return { ...state, playing: action.playing };
    case "failed":
      return { ...state, phase: "idle", notice: { tone: "error", text: action.text } };
    case "deciding":
      return state.ticket ? { ...state, ticket: { ...state.ticket, submitting: true } } : state;
    case "request_failed":
      return {
        ...state,
        ticket: state.ticket ? { ...state.ticket, submitting: false } : null,
        notice: { tone: "error", text: action.text },
      };
    case "verification_opened":
      return { ...state, verification: EMPTY_VERIFICATION };
    case "verification_closed":
      return { ...state, verification: null };
    case "verification_sending":
      return state.verification
        ? { ...state, verification: { ...state.verification, submitting: true, message: null } }
        : state;
    case "verification_problem":
      return state.verification
        ? {
            ...state,
            verification: { ...state.verification, submitting: false, message: action.text },
          }
        : state;
    case "socket_closed":
      if (state.phase === "connecting") {
        return {
          ...state,
          phase: "idle",
          playing: false,
          notice: state.notice ?? {
            tone: "error",
            text: "Could not reach the VoxCircuit server. Is the backend running?",
          },
        };
      }
      if (state.phase === "active") {
        return {
          ...state,
          phase: "ended",
          draft: "",
          userDraft: "",
          streaming: false,
          playing: false,
          notice: { tone: "error", text: "The connection to the server was lost." },
        };
      }
      return state;
  }
}

/** One call over WS /ws/call, in text or voice mode. */
export function useCall() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const socketRef = useRef<WebSocket | null>(null);
  const micRef = useRef<MicCapture | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  const liveRef = useRef(false); // call_started received: microphone frames may be sent.
  const tokenRef = useRef<string | null>(null); // This call's caller token, for REST calls.
  const startingRef = useRef(false);

  const stopAudio = useCallback(() => {
    micRef.current?.close();
    micRef.current = null;
    playerRef.current?.close();
    playerRef.current = null;
  }, []);

  const send = useCallback((message: ClientMessage) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
  }, []);

  const sendAudioFrame = useCallback((pcm: ArrayBuffer) => {
    const socket = socketRef.current;
    if (!liveRef.current || socket?.readyState !== WebSocket.OPEN) return;
    if (socket.bufferedAmount > MAX_SOCKET_BACKLOG_BYTES) return; // Live audio: drop, don't queue.
    socket.send(pcm);
  }, []);

  const openSocket = useCallback(
    (engine: Engine, inputMode: InputMode) => {
      const socket = new WebSocket(BACKEND_WS_URL);
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;
      socket.onopen = () => {
        // Start is only reachable after the caller ticks the consent notice.
        socket.send(
          JSON.stringify({
            type: "start",
            engine,
            input_mode: inputMode,
            consent: true,
          } satisfies ClientMessage),
        );
      };
      socket.onmessage = (event: MessageEvent) => {
        if (event.data instanceof ArrayBuffer) {
          playerRef.current?.push(event.data);
          return;
        }
        const message = parseServerMessage(event.data);
        if (!message) return;
        if (message.type === "call_started") {
          liveRef.current = true;
          tokenRef.current = message.session_token;
        }
        if (message.type === "interrupted") playerRef.current?.clear();
        if (message.type === "turn_complete") playerRef.current?.flush();
        if (message.type === "call_ended") {
          liveRef.current = false;
          stopAudio();
        }
        dispatch({ type: "server", message, at: Date.now() });
      };
      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null;
        liveRef.current = false;
        tokenRef.current = null;
        stopAudio();
        dispatch({ type: "socket_closed" });
      };
    },
    [stopAudio],
  );

  /** Call from the Start button's click handler, so audio can start. */
  const start = useCallback(
    async (engine: Engine, inputMode: InputMode) => {
      if (socketRef.current || startingRef.current) return;
      startingRef.current = true;
      dispatch({ type: "connect", inputMode });
      try {
        if (inputMode === "audio") {
          // Both AudioContexts are created synchronously here, inside the click.
          const [player, mic] = await Promise.allSettled([
            PcmPlayer.create((playing) => dispatch({ type: "playback", playing })),
            MicCapture.open(sendAudioFrame),
          ]);
          if (player.status === "rejected" || mic.status === "rejected") {
            if (player.status === "fulfilled") player.value.close();
            if (mic.status === "fulfilled") mic.value.close();
            dispatch({
              type: "failed",
              text:
                mic.status === "rejected"
                  ? describeMicError(mic.reason)
                  : "Could not start audio playback.",
            });
            return;
          }
          playerRef.current = player.value;
          micRef.current = mic.value;
        }
        openSocket(engine, inputMode);
      } finally {
        startingRef.current = false;
      }
    },
    [openSocket, sendAudioFrame],
  );

  /** A REST call on this call's behalf, with its caller token. Null if it never arrived. */
  const callApi = useCallback(
    async (path: string, method: "POST" | "DELETE", body?: unknown) => {
      const token = tokenRef.current;
      if (!token) return null;
      try {
        return await fetch(`${BACKEND_HTTP_URL}${path}`, {
          method,
          headers: {
            Authorization: `Bearer ${token}`,
            ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          },
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      } catch {
        return null;
      }
    },
    [],
  );

  /** Approve or cancel a booking ticket. The result arrives over the WebSocket. */
  const decide = useCallback(
    async (approvalId: string, decision: "approve" | "cancel") => {
      dispatch({ type: "deciding" });
      const response = await callApi(`/api/approvals/${approvalId}`, "POST", { decision });
      if (!response?.ok) {
        dispatch({
          type: "request_failed",
          text:
            response?.status === 404
              ? "That booking ticket is no longer available."
              : "Your answer could not be sent. Please try again.",
        });
      }
    },
    [callApi],
  );

  /** Ask for a code for the email on file. The demo code arrives over the WebSocket. */
  const startVerification = useCallback(
    async (email: string) => {
      dispatch({ type: "verification_sending" });
      const response = await callApi("/api/verify/start", "POST", { email });
      if (!response?.ok) {
        dispatch({
          type: "verification_problem",
          text:
            response?.status === 429
              ? "No more codes can be sent on this call."
              : "The code could not be sent. Check the email and try again.",
        });
      }
    },
    [callApi],
  );

  const checkCode = useCallback(
    async (code: string) => {
      dispatch({ type: "verification_sending" });
      const response = await callApi("/api/verify/check", "POST", { code });
      const result = response?.ok ? ((await response.json()) as { status: string }) : null;
      if (!result) {
        dispatch({ type: "verification_problem", text: "Enter the 6-digit code." });
      } else if (result.status === "no_challenge") {
        dispatch({
          type: "verification_problem",
          text: "There is no active code. Send a new one.",
        });
      }
    },
    [callApi],
  );

  const deleteMyData = useCallback(async () => {
    const response = await callApi("/api/customers/me", "DELETE");
    if (!response?.ok) {
      dispatch({
        type: "request_failed",
        text: "Your data could not be deleted. Please try again.",
      });
    }
  }, [callApi]);

  const openVerification = useCallback(() => dispatch({ type: "verification_opened" }), []);
  const closeVerification = useCallback(() => dispatch({ type: "verification_closed" }), []);

  const sendText = useCallback((text: string) => send({ type: "user_text", text }), [send]);
  const end = useCallback(() => send({ type: "end" }), [send]);
  const getAnalysers = useCallback(
    (): Analysers => ({
      mic: micRef.current?.analyser ?? null,
      player: playerRef.current?.analyser ?? null,
    }),
    [],
  );

  useEffect(() => {
    return () => {
      socketRef.current?.close();
      socketRef.current = null;
      stopAudio();
    };
  }, [stopAudio]);

  return {
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
  };
}
