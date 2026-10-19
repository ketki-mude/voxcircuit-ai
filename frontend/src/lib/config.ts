// NEXT_PUBLIC_ values are inlined at build time, so they must be read as literals.
export const BACKEND_WS_URL =
  process.env.NEXT_PUBLIC_BACKEND_WS_URL ?? "ws://localhost:8010/ws/call";
