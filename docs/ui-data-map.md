# Control room UI data map

Audit of what the team control room can show without backend changes. Every element of the
planned redesign is mapped to the endpoint and field that would supply it.

- **Yes**: returned by the API as is.
- **Derived**: computed in the browser from returned fields only (the fields are named).
- **No**: not returned by any endpoint. The element is hidden or shows "Not recorded".

Timestamps are ISO 8601 with a UTC offset, so they can be shown in the browser's time zone.
Appointment times are the exception: the API returns them only as preformatted text in the
business time zone (`America/New_York`), such as "Friday, October 23 at 9:00 AM EDT".

## 1. Endpoints the control room calls

| Endpoint | Role | Used by | Response fields |
| --- | --- | --- | --- |
| `POST /api/auth/login` | anyone | Sign-in | `access_token`, `token_type`, `user { id, email, name, role }` |
| `GET /api/auth/me` | viewer | Not called today (the session keeps the login `user`) | `id`, `email`, `name`, `role` |
| `GET /api/control-room/summary` | viewer | Overview (every 10 s) | `metrics { calls_today, booked_today, needs_human, policy_flags_today }`; `usage { calls_today, estimated_cost_usd_today, audio_minutes_in_today, audio_minutes_out_today, latency_p50_ms, latency_p95_ms, latency_samples, daily_call_cap }`; `settings { model, integrations_mode, max_call_seconds, max_turns, daily_call_cap, vad_silence_duration_ms, approval_ttl_seconds, business_timezone }` |
| `GET /api/handoffs?status=open\|taken\|all` | viewer | Overview queue (every 5 s) | list of `{ id, call_id, reason, label, status, created_at, taken_at, taken_by, call_active, call_started_at }`, oldest first, live calls only |
| `POST /api/handoffs/{id}/take-over` | agent | Overview queue | the updated handoff row; 404 unknown, 409 already taken |
| `GET /api/calls?purpose=live\|evaluation\|all&limit=1..200&offset` | viewer | Calls list (every 10 s) | list of `{ id, started_at, ended_at, duration_seconds, engine, input_mode, purpose, status, end_reason, flagged, user_turns, booked, handoff_reason, policy_flags, estimated_cost_usd }`, newest first |
| `GET /api/calls/{id}` | agent | Call detail (every 5 s) | `call { id, started_at, ended_at, engine, input_mode, model, purpose, status, end_reason, flagged, verified }`; `messages [{ seq, role, source, text, interrupted, latency_ms, created_at }]`; `tool_calls [{ name, status, duration_ms, args, result, created_at }]`; `approvals [{ id, action, status, slot, decided_by, created_at, decided_at, outcome, steps }]` where `outcome` is `null` or `{ success, slot_label, summary, steps [{ step, status, attempts }], appointment_id }` and `steps` is `[{ step, status, attempts, idempotency_key, last_error }]`; `handoffs [{ id, reason, label, status, created_at, taken_at, taken_by }]`; `policy_events [{ rule, excerpt, created_at }]`; `verification [{ status, attempts, created_at }]`; `usage { audio_seconds_in, audio_seconds_out, estimated_cost_usd, latency_p50_ms, latency_p95_ms, latency_samples }`; `audit [{ event, actor, payload, created_at }]` (up to 500) |
| `GET /api/evaluation/runs` | viewer | Evaluation (every 3 s) | latest 20 of `{ id, mode, status, requested_by, scenario_count, passed, task_success_rate, handoff_accuracy, unauthorized_writes, history_leaks, policy_flags, latency_p50_ms, latency_p95_ms, error, started_at, finished_at }` |
| `GET /api/evaluation/runs/{id}` | viewer | Evaluation | `run` (as above); `results [{ scenario_id, title, passed, expected, actual, failures, call_id, duration_ms }]` where `actual` is `{ booking, handoff, policy, verified, tool_errors, calendar_events, unauthorized_writes, history_leaks }` |
| `POST /api/evaluate` `{ mode }` | admin | Evaluation | 202 `{ run_id }`; 409 a run is in progress; 400 with the reason (no Gemini key, not enough daily cap left) |

Audit events that can appear in `audit[].event`: `call.started`, `call.consent`, `call.ended`,
`call.rejected`, `message.user`, `message.assistant`, `tool.called`, `tool.cancelled`,
`approval.created`, `approval.decided`, `approval.replaced`, `booking.started`,
`booking.completed`, `booking.slot_taken`, `handoff.created`, `handoff.taken`,
`policy.violation`, `system_notice.sent`, `verification.started`, `verification.attempt`,
`verification.limit`, `customer.deleted`.

## 2. UI element to data

### Navigation

| UI element | Endpoint | Field | Available |
| --- | --- | --- | --- |
| User name and role in the avatar menu | `POST /api/auth/login` (kept in the session) | `user.name`, `user.role` | Yes |
| User email in the menu | same | `user.email` | Yes |
| Sign out | none (the browser discards the token) | | Yes |
| Selected tab in the URL | existing routes `/control-room`, `/control-room/calls`, `/control-room/evaluation` | | Yes |

### Overview

| UI element | Endpoint | Field | Available |
| --- | --- | --- | --- |
| Calls today | summary | `metrics.calls_today` (live calls only) | Yes |
| Booked | summary | `metrics.booked_today` (appointments with status booked, live calls, created today) | Yes |
| Booked caption "of N calls" | summary | `metrics.calls_today` | Yes |
| Need a human | summary | `metrics.needs_human` (all open handoffs, not only today's) | Yes |
| Policy flags | summary | `metrics.policy_flags_today` | Yes |
| Captions "none waiting", "none today" | summary | the counts above equal 0 | Derived |
| Status bar: clear or queue | handoffs (open) | list is empty or not | Yes |
| Queue: caller name | handoffs | none | **No** |
| Queue: reason chip | handoffs | `label` (`reason` is the code) | Yes |
| Queue: time waiting | handoffs | now minus `created_at` | Derived |
| Queue: call still live | handoffs | `call_active` | Yes |
| Queue: Open call | handoffs | `call_id` (opening needs the agent role) | Yes |
| Queue: Take over | take-over | (agent or admin only) | Yes |
| Usage: "Model sessions: N of CAP" | summary | `usage.calls_today`, `usage.daily_call_cap` | Yes |
| Usage caption about evaluation runs | summary | `usage.calls_today` counts every call except scripted (offline) evaluation runs, so live evaluation runs are included | Yes |
| Estimated cost | summary | `usage.estimated_cost_usd_today` | Yes |
| Audio in and out | summary | `usage.audio_minutes_in_today`, `usage.audio_minutes_out_today` (minutes, 2 decimals) | Yes |
| Latency p50 and p95 | summary | `usage.latency_p50_ms`, `usage.latency_p95_ms` | Yes |
| "From N replies today" | summary | `usage.latency_samples` | Yes |
| Guardrail rules and descriptions | none: fixed text in `overview-page.tsx` that describes backend code | | **No** (static copy) |
| Chip "All enforced in code" | none | | **No** (static claim) |
| Chip "N of M enforced" | none | | **No** |
| Numbers inside guardrail text: ticket expiry, call length, turns, daily cap | summary | `settings.approval_ttl_seconds`, `settings.max_call_seconds`, `settings.max_turns`, `settings.daily_call_cap` | Yes |
| Numbers inside guardrail text: 6-digit code, 3 tries, 5 minutes | none (fixed in today's copy) | | **No** |

### Calls list

| UI element | Endpoint | Field | Available |
| --- | --- | --- | --- |
| Caller name | calls | none | **No** |
| Caller email (masked) | calls | none | **No** |
| Outcome chip: Booked | calls | `booked` | Yes |
| Outcome chip: Handed off | calls | `handoff_reason` (code only) | Yes |
| Handoff reason as a label | handoffs `?status=all`, matched on `call_id` | `label` | Derived (one extra request) |
| Outcome chip: Not booked | calls | neither `booked` nor `handoff_reason` | Derived |
| Why it was not booked (which step failed, or never asked to book) | calls | none | **No** (only in call detail) |
| Start time | calls | `started_at` | Yes |
| Duration (m:ss) | calls | `duration_seconds` (whole seconds, `null` while the call is live) | Yes |
| Duration with one decimal | calls | none | **No** (Derived in call detail) |
| Call id | calls | `id` (UUID; a short id is its first 8 characters) | Yes |
| Live call indicator | calls | `status` | Yes |
| Filter by outcome | calls | as above | Derived |
| Today's calls only | calls | `started_at` filtered in the browser (no date filter in the API; at most 200 rows per request) | Derived |

### Call detail (agent and admin only)

| UI element | Endpoint | Field | Available |
| --- | --- | --- | --- |
| Caller name, email, vehicle, ZIP, reason, current insurer, renewal month | call detail | `tool_calls[]` where `name = "create_or_update_lead"` and `status = "ok"`: `args`, merged in order. Only what Maya recorded | Derived |
| Full booking ticket as the caller saw it | none (stored in `approvals.ticket`, not returned) | | **No** |
| Verified returning caller | call detail | `call.verified` | Yes |
| Call id, start time | call detail | `call.id`, `call.started_at` | Yes |
| Duration (one decimal) | call detail | `call.ended_at` minus `call.started_at` | Derived |
| Outcome chip | call detail | `approvals[].outcome.success`, `handoffs[]` | Derived |
| Booking card: appointment date and time | call detail | `approvals[].slot` or `outcome.slot_label` (text in the business time zone) | Yes, as text |
| Appointment time in the browser's time zone | none (`appointments.starts_at` not returned) | | **No** |
| Caller approved, and when | call detail | `approvals[].status`, `decided_by`, `decided_at` | Yes |
| CRM saved | call detail | `approvals[].steps[]` where `step = "crm_upsert"`: `status`, `attempts`, `idempotency_key`, `last_error` | Yes |
| Calendar event created | call detail | `steps[]` where `step = "calendar_event"`: same fields | Yes |
| HubSpot contact id, Google Calendar event id | none (stored in `booking_steps.external_id` and `appointments.calendar_event_id`, not returned) | | **No** |
| Appointment id (internal) | call detail | `approvals[].outcome.appointment_id` | Yes |
| Plain note on what did and did not happen | call detail | `approvals[].outcome.summary` | Yes |
| Which step failed, and its error | call detail | `steps[].status = "failed"`, `last_error` | Yes |
| What the team should do next | none | | **No** (would be fixed copy per failed step; see decisions) |
| Handed off: reason, status, who took it, when | call detail | `handoffs[].label`, `status`, `taken_by`, `taken_at` | Yes |
| "Booked with" licensed agent | none | | **No**: omit |
| What happened timeline | call detail | `audit[]`: `event`, `actor`, `payload`, `created_at` | Yes |
| Metrics: latency p50 and p95 | call detail | `usage.latency_p50_ms`, `usage.latency_p95_ms` | Yes |
| Metrics: audio in and out | call detail | `usage.audio_seconds_in`, `usage.audio_seconds_out` | Yes |
| Metrics: estimated cost | call detail | `usage.estimated_cost_usd` | Yes |
| Metrics: policy flags | call detail | count of `policy_events`; excerpts in `policy_events[].excerpt` | Derived |
| Transcript | call detail | `messages[]` | Yes |
| "From the audit log": consent recorded | call detail | `audit[]` event `call.consent` | Yes |
| "From the audit log": approved before any booking write | call detail | `approval.decided` (status approved) before `booking.started` | Derived |
| "From the audit log": booking steps result | call detail | `booking.completed` `payload.steps` | Yes |
| "From the audit log": policy flag was corrected | call detail | `policy.violation` followed by `system_notice.sent` | Derived |
| "From the audit log": verified before history was read | call detail | `verification.attempt` with `payload.result = "verified"` before `tool.called` for `get_previous_interactions` | Derived |
| "From the audit log": AI disclosure | none (no event; only Maya's first message text) | | **No**: omit |

### Evaluation

| UI element | Endpoint | Field | Available |
| --- | --- | --- | --- |
| Run scripted, Run live | evaluate | (admin only) | Yes |
| Confirm dialog: sessions left today | summary | `usage.daily_call_cap` minus `usage.calls_today` | Derived |
| Scenario counts in the intro line (30 scripted, about 5 live) | none before a run | | **No**; Derived from the latest run of each mode (`scenario_count`) when one exists |
| Run header: mode, time, status | runs | `mode`, `started_at`, `status` | Yes |
| "All passed" or "N failed" | runs | `scenario_count` minus `passed` | Derived |
| Task success and "N of M scenarios" | runs | `task_success_rate`, `passed`, `scenario_count` | Yes |
| Handoff accuracy | runs | `handoff_accuracy` | Yes |
| Handoff accuracy "N of M" | run detail | `results[].expected.handoff` vs `results[].actual.handoff` | Derived |
| Latency p50, caption p95 | runs | `latency_p50_ms`, `latency_p95_ms` | Yes |
| Unauthorized writes, history leaks, policy flags | runs | `unauthorized_writes`, `history_leaks`, `policy_flags` | Yes |
| Scenario title and result | run detail | `results[].title`, `passed` | Yes |
| Scenario outcome ("No booking", "Booked", "Handed off: reason") | run detail | `results[].actual.booking`, `actual.handoff` (codes) | Yes |
| Scenario time in seconds | run detail | `results[].duration_ms` | Yes |
| Open the scenario's call | run detail | `results[].call_id` (agent role) | Yes |
| Scenario id and failure reasons | run detail | `results[].scenario_id`, `failures` | Yes |
| Run history | runs | latest 20 runs, `requested_by`, `error` | Yes |

### Bookings

| UI element | Endpoint | Field | Available |
| --- | --- | --- | --- |
| Appointments for a chosen date | none | | **No** |
| Appointment start and end as timestamps | none (`appointments.starts_at` not returned) | | **No** |
| Caller on each appointment | none (only through one call-detail request per booked call, agent role) | | **No** |
| Agent column per appointment | none | | **No** |
| Prep brief | none | | **No** |

**Result: the Bookings tab is not supported by today's data and is left out.**

## 3. Nice to have, not available

Not implemented. Each would need backend work.

1. Caller name and email on the calls list and the handoff queue.
2. An appointments endpoint for a date (start, end, status, call id, caller name). This unlocks the Bookings tab.
3. Appointment start and end as timestamps in call detail, so they can be shown in the browser's time zone.
4. External ids: the HubSpot contact id and Google Calendar event id (stored, not returned).
5. The licensed agent assigned to a booking ("Booked with").
6. A prep brief for the agent.
7. Server-computed compliance checks, including an AI-disclosure event.
8. A guardrails list with enforcement status from the server ("N of M enforced").
9. Verification limits (code length, tries, expiry) in `settings`.
10. Scenario counts per mode before a run (from `scenarios.yaml`).
11. A date filter on `GET /api/calls`.
12. Why a call was not booked (failed step) and the handoff label on the calls list.
13. The full booking ticket snapshot in call detail.

## 4. Decisions needed before restyling

1. **Calls list without caller names.** Rows would lead with time and short call id. "Caller" in
   the list and queue shows nothing (hidden) rather than "Not recorded" on every row.
2. **Email masking.** No list shows an email today. The masking helper is built and tested, and
   is used wherever an email appears outside call detail.
3. **Bookings tab.** Skipped (section 2).
4. **Guardrails.** The rules are fixed copy describing backend code. Keep them as copy with the
   chip "Enforced in code", using `settings` values for the numbers it has, and drop the
   verification numbers that are not served (or keep them as copy).
5. **Evaluation intro line.** Use scenario counts from the latest run of each mode, and omit the
   count when there is no run yet, instead of fixed "30" and "about 5".
6. **Viewers on the Calls page.** Call detail is agent-only, so viewers get the list and a note
   in the detail pane.
7. **"What the team should do" for a failed booking.** Either fixed guidance per failed step
   (for example "CRM saved, calendar failed: add the event to the calendar") or only the facts
   (step, attempts, error).
8. **Appointment times** are shown as the server's text in the business time zone.
9. **Frontend tests** need a test runner. None exists today. Proposed: Vitest with React
   Testing Library and jsdom, as dev dependencies only.
