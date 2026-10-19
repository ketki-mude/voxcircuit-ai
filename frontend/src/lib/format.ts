// Display helpers. Missing values show as "--", never as a made-up number.

export const DASH = "--";

export function formatTime(iso: string | null): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatSeconds(seconds: number | null): string {
  if (seconds === null) return DASH;
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function formatUsd(value: number | null): string {
  return value === null ? DASH : `$${value.toFixed(4)}`;
}

export function formatPercent(ratio: number | null): string {
  return ratio === null ? DASH : `${Math.round(ratio * 100)}%`;
}

export function formatMs(ms: number | null): string {
  return ms === null ? DASH : `${ms.toLocaleString()} ms`;
}

export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : value.toLocaleString();
}
