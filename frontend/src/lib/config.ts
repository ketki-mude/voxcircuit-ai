// NEXT_PUBLIC_ values are inlined at build time, so they must be read as literals.
export const BACKEND_WS_URL =
  process.env.NEXT_PUBLIC_BACKEND_WS_URL ?? "ws://localhost:8010/ws/call";

/** The backend's HTTP origin, derived from the WebSocket URL (ws -> http, wss -> https). */
export const BACKEND_HTTP_URL = (() => {
  const url = new URL(BACKEND_WS_URL);
  url.protocol = url.protocol === "wss:" ? "https:" : "http:";
  return url.origin;
})();
