"use client";

import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
} from "react";

import { BACKEND_HTTP_URL } from "@/lib/config";
import { ApiError, type LoginResponse, type TeamUser } from "@/lib/team-api";

// The team token lives in sessionStorage: it survives a reload, but not closing the tab.
const STORAGE_KEY = "voxcircuit.team";
const CHANGE_EVENT = "voxcircuit-team-session";

interface StoredSession {
  token: string;
  user: TeamUser;
}

function readStorage(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStorage(session: StoredSession | null): void {
  try {
    if (session) window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage blocked: the session lasts until the page reloads.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

interface TeamSession {
  /** False until the browser has been checked for a saved session. */
  ready: boolean;
  user: TeamUser | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => void;
  /** A control-room API call with the team token. A 401 signs the user out. */
  api: <T>(path: string, init?: RequestInit) => Promise<T>;
}

const TeamSessionContext = createContext<TeamSession | null>(null);

export function TeamSessionProvider({ children }: { children: ReactNode }) {
  const raw = useSyncExternalStore(subscribe, readStorage, () => null);
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    // Mark that the client snapshot is now in use (the server snapshot is always empty).
    const id = requestAnimationFrame(() => setHydrated(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const session = useMemo<StoredSession | null>(() => {
    if (!raw) return null;
    try {
      return JSON.parse(raw) as StoredSession;
    } catch {
      return null;
    }
  }, [raw]);
  const token = session?.token ?? null;

  const signOut = useCallback(() => writeStorage(null), []);

  const signIn = useCallback(async (email: string, password: string) => {
    let response: Response;
    try {
      response = await fetch(`${BACKEND_HTTP_URL}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
    } catch {
      throw new ApiError(0, "Could not reach the VoxCircuit server.");
    }
    if (!response.ok) {
      throw new ApiError(response.status, await errorMessage(response));
    }
    const body = (await response.json()) as LoginResponse;
    writeStorage({ token: body.access_token, user: body.user });
  }, []);

  const api = useCallback(
    async <T,>(path: string, init?: RequestInit): Promise<T> => {
      if (!token) throw new ApiError(401, "Sign in required");
      let response: Response;
      try {
        response = await fetch(`${BACKEND_HTTP_URL}${path}`, {
          ...init,
          headers: {
            ...(init?.body ? { "Content-Type": "application/json" } : {}),
            ...init?.headers,
            Authorization: `Bearer ${token}`,
          },
        });
      } catch {
        throw new ApiError(0, "Could not reach the VoxCircuit server.");
      }
      if (response.status === 401) {
        writeStorage(null);
        throw new ApiError(401, "Your session expired. Sign in again.");
      }
      if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
      return (await response.json()) as T;
    },
    [token],
  );

  const value = useMemo<TeamSession>(
    () => ({ ready: hydrated, user: session?.user ?? null, signIn, signOut, api }),
    [hydrated, session, signIn, signOut, api],
  );
  return <TeamSessionContext.Provider value={value}>{children}</TeamSessionContext.Provider>;
}

export function useTeamSession(): TeamSession {
  const session = useContext(TeamSessionContext);
  if (!session) throw new Error("useTeamSession needs a TeamSessionProvider");
  return session;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Not JSON.
  }
  return `The server answered ${response.status}.`;
}

/** Load a control-room resource, refreshing every `intervalMs` (0 means once). */
export function useTeamResource<T>(path: string | null, intervalMs = 0) {
  const { api } = useTeamSession();
  const [state, setState] = useState<{ data: T | null; error: string | null }>({
    data: null,
    error: null,
  });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api<T>(path);
        if (!cancelled) setState({ data, error: null });
      } catch (error) {
        if (!cancelled) {
          const message = error instanceof Error ? error.message : "Something went wrong.";
          setState((previous) => ({ data: previous.data, error: message }));
        }
      }
    };
    void load();
    const id = intervalMs > 0 ? setInterval(() => void load(), intervalMs) : undefined;
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [api, path, intervalMs, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { ...state, reload };
}
