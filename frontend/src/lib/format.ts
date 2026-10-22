// Display helpers. Missing values show as "--", never as a made-up number.
// Times are shown in the browser's time zone, with its abbreviation (10:00 AM EDT).

export const DASH = "--";

export function formatTime(iso: string | null): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

/** With seconds, for events that happen close together: 10:42:13 AM EDT. */
export function formatTimeSeconds(iso: string | null): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

/** The exact moment, for tooltips on relative times. */
export function formatExact(iso: string | null): string {
  if (!iso) return DASH;
  return new Date(iso).toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
}

/** How long ago `iso` was, relative to `now` (ms): "under 1 min", "4 min", "1 h 5 min". */
export function formatElapsed(iso: string, now: number): string {
  const minutes = Math.floor(Math.max(0, now - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "under 1 min";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 === 0 ? `${hours} h` : `${hours} h ${minutes % 60} min`;
}

/** m:ss, from seconds. */
export function formatSeconds(seconds: number | null): string {
  if (seconds === null) return DASH;
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** m:ss, from minutes (the summary reports audio in minutes). */
export function formatMinutes(minutes: number | null): string {
  return minutes === null ? DASH : formatSeconds(minutes * 60);
}

/** Seconds with one decimal: 14.7 s. */
export function formatSecondsDecimal(seconds: number | null): string {
  return seconds === null ? DASH : `${seconds.toFixed(1)} s`;
}

/** A call's length: 14.7 s under a minute, m:ss from a minute on. */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return DASH;
  return seconds < 60 ? formatSecondsDecimal(seconds) : formatSeconds(seconds);
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

/** "1 reply", "2 replies": a count with the right noun. */
export function plural(count: number, one: string, many: string): string {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}
