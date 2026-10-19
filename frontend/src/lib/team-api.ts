// Team control room API types. Mirrors backend/app/api/{team_auth,control_room,evaluation}.py.

export type Role = "viewer" | "agent" | "admin";

export interface TeamUser {
  id: string;
  email: string;
  name: string;
  role: Role;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  user: TeamUser;
}

export interface Summary {
  metrics: {
    calls_today: number;
    booked_today: number;
    needs_human: number;
    policy_flags_today: number;
  };
  usage: {
    calls_today: number;
    daily_call_cap: number;
    estimated_cost_usd_today: number;
    audio_minutes_in_today: number;
    audio_minutes_out_today: number;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
    latency_samples: number;
  };
  settings: {
    model: string;
    integrations_mode: string;
    max_call_seconds: number;
    max_turns: number;
    daily_call_cap: number;
    vad_silence_duration_ms: number;
    approval_ttl_seconds: number;
    business_timezone: string;
  };
}

export interface HandoffRow {
  id: string;
  call_id: string;
  reason: string;
  label: string;
  status: "open" | "taken" | "closed";
  created_at: string;
  taken_at: string | null;
  taken_by: string | null;
  call_active: boolean;
  call_started_at: string;
}

export interface CallRow {
  id: string;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  engine: string;
  input_mode: string;
  purpose: "live" | "evaluation";
  status: string;
  end_reason: string | null;
  flagged: boolean;
  user_turns: number;
  booked: boolean;
  handoff_reason: string | null;
  policy_flags: number;
  estimated_cost_usd: number;
}

export interface CallDetail {
  call: {
    id: string;
    started_at: string;
    ended_at: string | null;
    engine: string;
    input_mode: string;
    model: string | null;
    purpose: string;
    status: string;
    end_reason: string | null;
    flagged: boolean;
    verified: boolean;
  };
  messages: {
    seq: number;
    role: "user" | "assistant" | "system";
    source: string;
    text: string;
    interrupted: boolean;
    latency_ms: number | null;
    created_at: string;
  }[];
  tool_calls: {
    name: string;
    status: "ok" | "error";
    duration_ms: number;
    args: Record<string, unknown>;
    result: Record<string, unknown> | null;
    created_at: string;
  }[];
  approvals: {
    id: string;
    action: string;
    status: string;
    slot: string;
    decided_by: string | null;
    created_at: string;
    decided_at: string | null;
    outcome: { success: boolean; summary: string } | null;
    steps: {
      step: string;
      status: string;
      attempts: number;
      idempotency_key: string;
      last_error: string | null;
    }[];
  }[];
  handoffs: {
    id: string;
    reason: string;
    label: string;
    status: string;
    created_at: string;
    taken_at: string | null;
    taken_by: string | null;
  }[];
  policy_events: { rule: string; excerpt: string; created_at: string }[];
  verification: { status: string; attempts: number; created_at: string }[];
  usage: {
    audio_seconds_in: number;
    audio_seconds_out: number;
    estimated_cost_usd: number;
    latency_p50_ms: number | null;
    latency_p95_ms: number | null;
    latency_samples: number;
  };
  audit: { event: string; actor: string; payload: Record<string, unknown>; created_at: string }[];
}

export interface EvaluationRun {
  id: string;
  mode: "scripted" | "live";
  status: "running" | "completed" | "failed";
  requested_by: string | null;
  scenario_count: number;
  passed: number;
  task_success_rate: number | null;
  handoff_accuracy: number | null;
  unauthorized_writes: number | null;
  history_leaks: number | null;
  policy_flags: number | null;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface EvaluationResultRow {
  scenario_id: string;
  title: string;
  passed: boolean;
  expected: Record<string, unknown>;
  actual: Record<string, unknown>;
  failures: string[];
  call_id: string | null;
  duration_ms: number;
}

export interface RunDetail {
  run: EvaluationRun;
  results: EvaluationResultRow[];
}

const RANK: Record<Role, number> = { viewer: 0, agent: 1, admin: 2 };

export function hasRole(user: TeamUser | null, minimum: Role): boolean {
  return user !== null && RANK[user.role] >= RANK[minimum];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
