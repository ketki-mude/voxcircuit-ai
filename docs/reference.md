# Technical reference

Details for developers. Start with the [README](../README.md) for the overview and setup.

## Contents

- [Configuration](#configuration)
- [How a call works](#how-a-call-works)
- [Booking: approval, then the workflow](#booking-approval-then-the-workflow)
- [Returning customers, handoffs and the policy monitor](#returning-customers-handoffs-and-the-policy-monitor)
- [Evaluation](#evaluation)
- [API](#api)
- [Gemini Live session details](#gemini-live-session-details)
- [Data and privacy](#data-and-privacy)
- [Tests and CI](#tests-and-ci)
- [Known limits](#known-limits)
- [Project layout](#project-layout)

## Configuration

Backend settings come from environment variables or `backend/.env`
(see `backend/.env.example`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://localhost:5432/voxcircuit` | App database |
| `TEST_DATABASE_URL` | `postgresql+psycopg://localhost:5432/voxcircuit_test` | Used by pytest only |
| `FRONTEND_ORIGINS` | `http://localhost:3000` | Comma-separated origins allowed to call the backend |
| `APP_SECRET_KEY` | random at startup | Signs caller and team tokens; use 32+ characters. If unset, everyone is signed out on restart |
| `GEMINI_API_KEY` | empty | Gemini API key. Empty means only the offline model works |
| `GEMINI_LIVE_MODEL` | `gemini-3.8-live` | Live API model |
| `VAD_SILENCE_DURATION_MS` | `700` | Silence that ends a caller turn |
| `MAX_CALL_SECONDS` | `300` | A call ends after this long |
| `MAX_TURNS` | `30` | Caller turns per call |
| `DAILY_CALL_CAP` | `20` | New calls are refused once this many started today (server's local day). Scripted evaluation calls don't count; live ones do |
| `AUDIO_COST_PER_MIN_IN` | `0.005` | USD per minute of caller audio, for estimates |
| `AUDIO_COST_PER_MIN_OUT` | `0.018` | USD per minute of model audio, for estimates |
| `INTEGRATIONS_MODE` | `mock` | `mock` or `live` (HubSpot and Google Calendar) |
| `BUSINESS_TIMEZONE` | `America/New_York` | Consultations are weekdays 9:00 to 17:00 in this time zone |
| `APPROVAL_TTL_SECONDS` | `300` | A booking ticket expires after this long |
| `HUBSPOT_PRIVATE_APP_TOKEN` | empty | Live mode: HubSpot service key (or legacy private app token) |
| `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` | empty | Live mode: Desktop app OAuth client |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | empty | Live mode: from `python -m app.integrations.google_oauth` |
| `GOOGLE_CALENDAR_ID` | empty | Live mode: the dedicated test calendar |

The cost defaults are the paid-tier list prices for `gemini-3.8-live` as of October 2026
(<https://ai.google.dev/gemini-api/docs/pricing>). The free tier is not billed.

Frontend: `NEXT_PUBLIC_BACKEND_WS_URL` in `frontend/.env.local` (default
`ws://localhost:8010/ws/call`). The REST origin is derived from it. It is fixed at build
time. Docker Compose: `POSTGRES_PASSWORD` in the root `.env`.

## How a call works

1. In a voice call, the browser first opens the microphone and the audio player.
2. The browser opens `/ws/call` and sends a start message with the model, the mode, and
   consent.
3. The backend checks consent and the daily cap, creates the `calls` row, connects the
   Live session, and replies `call_started` with the caller token.
4. Caller audio goes up as 16 kHz PCM frames; Maya's audio comes back as 24 kHz PCM through
   a jitter buffer. Transcripts are saved as one `messages` row per turn.
5. **Barge-in**: when the caller talks over Maya, the browser clears its playback queue.
6. When the model calls a tool, the backend validates and runs it, logs it to `tool_calls`,
   and sends a `tool` event for the circuit timeline.
7. Facts only the backend knows (booking results, verification, handoffs, take-over,
   compliance corrections) reach the model as turns starting with `[Northstar system]`.
   Typed caller text that imitates that prefix is rejected.
8. The call ends when the caller ends it, a cap is hit, a team member takes it over, the
   socket closes, or the Live API fails.

### Latency

Measured on the backend for each of Maya's replies: from the caller's last voiced audio
frame (or the moment a typed turn or system notice was sent) to Maya's first audio chunk.
It includes the silence the voice activity detector waits for, and excludes the browser's
120 ms playback buffer. It is stored in `messages.latency_ms`; the control room reports p50
and p95.

## Booking: approval, then the workflow

1. `request_booking` stores a pending approval with a snapshot of the ticket.
2. The caller presses Approve or Cancel (`POST /api/approvals/{id}` with the caller token).
   The decision is recorded once, so a double click cannot book twice.
3. On Approve the backend holds the slot (the slot's primary key prevents double booking),
   then runs `crm_upsert` and `calendar_event`. Each step has a row in `booking_steps` with
   a status and a unique idempotency key, and gets one retry.
4. The booking succeeds only if both steps are done. Otherwise the successful step stays
   recorded, the call is flagged, a handoff opens, and the summary says what did and did
   not happen.
5. The result goes to the caller's screen and to the model, and Maya announces it.

Mock integrations (mock mode only): `GET /mock/calendar/events`, `GET /mock/crm/contacts`,
and `PUT /mock/calendar/faults` or `/mock/crm/faults` with a mode of `ok`, `fail_once`,
`fail_always` or `fail_after_write_once`.

## Returning customers, handoffs and the policy monitor

- **Verification** is the only code that marks a call verified: a 6-digit code, 3 tries,
  5 minutes, at most 3 codes per call, stored as hashes. `get_previous_interactions` takes no
  arguments and returns only the verified customer's first name, vehicle and last
  appointment.
- **Handoffs** open (one per call) when the caller has a claim or complaint, asks for a
  person, or is upset; when an out-of-scope request persists; when a detail is corrected
  twice or a tool fails twice; when verification fails; when a booking is incomplete; or
  when Maya asks.
- **Policy monitor.** Speech can't be blocked before it plays, so policy violations are
  detected, flagged, and corrected. Every finalized assistant transcript is checked for
  prices, approval promises, coverage advice, and booking claims without a confirmed
  booking. A violation is stored, flags the call, and makes Maya retract the statement.
- **Delete my data** (`DELETE /api/customers/me`, verified callers only) removes the
  customer, their calls, transcripts, leads, appointments and verification records, the CRM
  contact and their calendar events. Audit logs stay (ids only).

## Evaluation

`backend/app/evaluation/scenarios.yaml` holds **30 scripted scenarios** and **5 live
ones**. A scripted scenario lists Maya's turns (played by `FakeLiveSession`), the caller's
steps (say something, approve, cancel, enter a right or wrong code, another caller taking
the slot, hang up), optional setup (CRM or calendar faults, seeded returning customers),
and what must be true afterwards.

They cover: the happy path; a returning customer rescheduling; cancel at approval; changing
the time before approving; double booking; CRM up and calendar down, the reverse, and both
down; a retry that recovers; a timeout after the write; rescheduling in the same call; a
ticket expiring when the caller hangs up; booking before details are complete; approving
twice; price and coverage questions declined and persisting; Maya stating a price, giving
coverage advice, promising approval, or claiming a booking that did not happen; an angry
caller; a claim; a complaint; asking for a person; correcting the same detail twice; an
offered Social Security number; returning customers verifying and failing to; and a caller
trying to get another person's history.

The runner (`backend/app/evaluation/runner.py`) plays each scenario through the real call
handler, tools, booking workflow and safety rules, with its own mock calendar and CRM. It
never touches the app's integrations, even in live-integrations mode.

| Metric | Meaning |
| --- | --- |
| Task success | Scenarios where every expectation held |
| Handoff accuracy | Scenarios where the handoff (or none) matched the expected one |
| Unauthorized writes | CRM or calendar writes, leads, appointments or booking steps without an approved approval. **Must be 0** |
| History leaks | History returned before verification, or another person's details in a tool result or reply. **Must be 0** |
| Policy flags | Policy events across the run's calls |
| Latency p50 and p95 | Assistant-turn latency across the run. Near zero in scripted runs, where replies are instant |

Run it from the Evaluation page, or from the command line (exits non-zero if any scenario
fails):

```bash
cd backend
python -m app.evaluation               # the 30 scripted scenarios, no network
python -m app.evaluation --mode live   # the live scenarios against Gemini Live
```

**Live mode** sends text turns to the real model (`GEMINI_API_KEY` required). Each live
scenario is one call against `DAILY_CALL_CAP`, and a run is refused if not enough of
today's cap is left. Its checks are about behavior: Maya discloses that she is an AI, states
no prices or coverage advice, hands off a claim, and books after approval.

## API

Caller endpoints need the caller token from `call_started`; team endpoints need a team
token from `POST /api/auth/login`.

| Endpoint | Who | Purpose |
| --- | --- | --- |
| `WS /ws/call` | caller | The call |
| `POST /api/approvals/{id}` | caller | Approve or cancel a ticket |
| `POST /api/verify/start`, `POST /api/verify/check` | caller | Verification |
| `DELETE /api/customers/me` | caller (verified) | Delete my data |
| `POST /api/auth/login`, `GET /api/auth/me` | team | Sign in |
| `GET /api/control-room/summary` | viewer | Metrics, usage, settings |
| `GET /api/calls`, `GET /api/handoffs` | viewer | Calls list, handoff queue |
| `GET /api/calls/{id}` | agent | Call detail with transcript |
| `POST /api/handoffs/{id}/take-over` | agent | Take over a call |
| `GET /api/evaluation/runs`, `GET /api/evaluation/runs/{id}` | viewer | Evaluation results |
| `POST /api/evaluate` | admin | Start a run: `{"mode": "scripted" \| "live"}` |

WebSocket messages are listed in `backend/app/calls/protocol.py` (mirrored in
`frontend/src/lib/call-protocol.ts`).

## Gemini Live session details

Checked against google-genai 2.31 and the Live API docs (October 2026): AUDIO responses
with output transcription (native audio models don't do TEXT); input and output
transcription; automatic voice activity detection; blocking tools declared from the Pydantic
schemas; context window compression; transparent reconnects with session resumption on
`go_away`; quota errors end the call with a friendly message.

## Data and privacy

- Only text transcripts are stored. Audio is never stored.
- `audit_logs` is append-only: a database trigger rejects `UPDATE`, `DELETE` and `TRUNCATE`.
  It records messages, tool calls, approvals, bookings, handoffs, take-overs, policy events,
  verification attempts, sign-ins and evaluation runs, with ids and counts only.
- Request logs of the HTTP client are kept at warning level, because CRM lookup URLs
  contain email addresses.
- Calendar events carry the caller's first name only; the CRM holds the details.

## Tests and CI

```bash
# Backend (from backend/, with the venv active)
pytest                      # needs the voxcircuit_test database
ruff check . && ruff format --check .
mypy app tests

# Frontend (from frontend/)
npm run lint
npm run typecheck           # runs next typegen, then tsc
npm run build
```

The tests drop and rebuild the schema of `TEST_DATABASE_URL` (default
`postgresql+psycopg://localhost:5432/voxcircuit_test`) by running the Alembic migrations.
They refuse to run against a database whose name does not end in `_test`. They include
whole conversations with `FakeLiveSession`, all 30 evaluation scenarios, role checks, and
the HubSpot and Google clients against fakes of each API (`httpx.MockTransport`).

`.github/workflows/ci.yml` runs the same checks on every push to `main` and on pull
requests: a backend job with a Postgres 16 service, and a frontend job. It deploys
nothing.

## Known limits

- Checked against the real services on 19 October 2026: the live evaluation passed 5 of 5
  against Gemini Live, and HubSpot and Google Calendar passed write-and-delete checks. A full
  voice call with a real microphone has not been tested by the developers yet; the browser was
  tested in headless Chrome with scripted sessions and a fake microphone.
- Handoff triggers and policy rules are keyword and pattern rules: they can miss unusual
  phrasing and occasionally fire on harmless sentences.
- The demo shows the verification code on screen, so anyone on the call can verify as any
  email they know. Real use needs the code sent to the phone on file.
- "Take over call" hands the call to a person in the records and ends the AI side; it does
  not bridge a human into the audio.
- Team sign-in has no rate limiting or password reset yet, and the team token is kept in
  the browser's session storage.
- Calls, notices and evaluation runs live in one process; run a single uvicorn worker.
- The mock calendar and CRM keep their data in memory; a restart clears them.
- Without headphones, Maya can hear and interrupt herself.

## Project layout

```
backend/
  app/
    main.py              App factory and wiring
    config.py            Settings (env / .env)
    models.py            SQLAlchemy models
    auth.py              Caller and team tokens, passwords, roles
    cli.py               python -m app.cli create-user
    reporting.py         Control-room queries
    customers.py         Previous-interaction summary, delete my data
    prompts.py           Maya's system instruction
    api/                 /ws/call and the REST endpoints
    booking/             Slots, approvals, the booking workflow
    calls/               Call handler, protocol, recorder, registry, usage, latency
    evaluation/          scenarios.yaml, runner, checks, service, CLI
    integrations/        Interfaces, mocks, HubSpot, Google Calendar, OAuth setup
    live/                LiveSession interface, Gemini and fake sessions
    safety/              Handoff rules, policy monitor
    tools/               Tool specs (Pydantic) and the executor
    verification/        Verification challenges
  alembic/               Migrations 0001 to 0005
  tests/
frontend/
  public/worklets/       AudioWorklets: mic capture and jitter-buffered player
  src/app/               Customer page; control-room pages
  src/components/call/   Customer call UI
  src/components/team/   Control room UI
  src/hooks/             Call state; team session
  src/lib/               Protocol and API types, config, formatting, audio
docs/                    This reference, integration setup, screenshots
docker-compose.yml       Postgres, backend, frontend
.github/workflows/ci.yml Lint, type checks and tests
```
