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
import { BACKEND_WS_URL } from "@/lib/config";

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
    case "call_ended":
      return {
        ...state,
        phase: "ended",
        draft: "",
        userDraft: "",
        streaming: false,
        playing: false,
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
        if (message.type === "call_started") liveRef.current = true;
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

  return { state, start, sendText, end, getAnalysers };
}
