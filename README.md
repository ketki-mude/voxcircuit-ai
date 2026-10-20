# VoxCircuit AI

**Conversations that turn into actions.**

A real-time voice agent for auto insurance intake. Maya, an AI assistant for the fictional
Northstar Insurance, qualifies leads, books or reschedules a consultation with a licensed
human agent, and recognizes verified returning customers. She does not quote prices,
recommend coverage, promise approval, or give legal or financial advice.

> Demo only. Northstar Insurance is fictional. Use fake details only: on the free tier,
> Google may use prompts and audio to improve its products.

## Status

All seven build steps are in place. Nothing is deployed.

| Built | Not built |
| --- | --- |
| Voice (16 kHz mic, 24 kHz playback with a jitter buffer, barge-in) and text calls | Deployment |
| Typed tools; bookings only after the caller approves a ticket | Guided call (scripted voice demo) |
| Two-step booking (CRM, calendar) with idempotency and honest partial failures | |
| Returning-customer verification, handoffs, policy monitor, "delete my data" | |
| Team control room, Calls page, Evaluation page; team login with roles | |
| 30 scripted evaluation scenarios plus a small live mode | |
| Mock integrations, plus HubSpot and Google Calendar behind `INTEGRATIONS_MODE` | |
| Docker Compose; GitHub Actions for lint, type checks and tests | |

Not yet exercised against real services (no credentials were available while building):
the Gemini Live API, HubSpot and Google Calendar. Their clients are tested against fakes of
each API; see [Known limits](#known-limits).

## Architecture

```
Caller's browser (Next.js)  --JSON + PCM over WebSocket-->  FastAPI /ws/call  <-->  LiveSession
   |  REST with a caller token (approve, verify, delete)        |                      |-- Gemini Live API
Team browser (control room) --REST with a team token-->          |                      '-- FakeLiveSession
                                                                 v
                                    PostgreSQL     Calendar (mock or Google)     CRM (mock or HubSpot)
```

- The Gemini API key and integration credentials live only in `backend/.env`.
- The backend owns the Live session. It runs every tool call, owns every write, relays
  audio both ways, stores transcripts (text only, never audio), and enforces the rules.
- `CallHandler` (`backend/app/calls/handler.py`) runs one call. It only sees
  provider-neutral events, so the fake and the real session are interchangeable.
- Two kinds of tokens, both signed with `APP_SECRET_KEY`: a **caller token** issued at call
  start for that call's REST endpoints, and a **team token** from team sign-in.

## Prerequisites

- macOS with Homebrew PostgreSQL 16 running: `brew services start postgresql@16`
- Python 3.11 or newer (3.12 is used below)
- Node.js 20.9 or newer (required by Next.js 16)
- Optional: a free Gemini API key. Without one, use the **Offline test** model.
- Optional: Docker Desktop, to run everything with Docker Compose.

### Get a Gemini API key (free tier, no billing)

1. Open <https://aistudio.google.com/apikey> and sign in with a Google account.
2. Click **Create API key**. Do not set up billing; the free tier needs none.
3. Paste the key into `backend/.env` as `GEMINI_API_KEY=...` (see below). Never commit it.

## Setup

### 1. Databases

```bash
createdb voxcircuit        # app database
createdb voxcircuit_test   # test database; the test suite resets it on every run
```

### 2. Backend

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # then set GEMINI_API_KEY and APP_SECRET_KEY
alembic upgrade head          # run again after every pull that adds a migration
uvicorn app.main:app --reload --port 8010
```

Check it: `curl localhost:8010/api/health` should print `{"status":"ok"}`.

Create a control room user (the password is asked for twice, at least 12 characters):

```bash
python -m app.cli create-user --email you@example.com --name "Your Name" --role admin
```

The backend runs on port 8010 because port 8000 is often taken by other local projects. If
you change the port, update `NEXT_PUBLIC_BACKEND_WS_URL` in the frontend too.

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open <http://localhost:3000>. The header switches between **Customer view** and **Team
control room**.

### Or: Docker Compose

```bash
cp .env.example .env          # set POSTGRES_PASSWORD (letters and digits)
docker compose up --build
docker compose exec backend python -m app.cli create-user \
    --email you@example.com --name "Your Name" --role admin
```

This runs Postgres, the backend (migrations run on start) and a production build of the
frontend at <http://localhost:3000>. The backend also reads `backend/.env` if it exists.
Postgres is not published on the host, so it doesn't clash with a local one on 5432; stop
the local dev servers first, since ports 8010 and 3000 are used. `docker compose down -v`
removes the database volume.

## Using it

### Customer view

1. Tick **I understand** on the consent banner. The backend refuses to start a call without it.
2. Pick a model: **Gemini Live** (needs `GEMINI_API_KEY`) or **Offline test** (a short
   scripted intake, text only, no network).
3. Pick **Voice** or **Text**. For voice, **use headphones**, otherwise Maya can hear
   herself through your speakers and interrupt her own reply.
4. Click **Start call**. Maya greets you and collects your details.
5. When you pick a time, a **Review before I book** ticket appears. Nothing is booked until
   you press **Approve**. **Cancel** books nothing.
6. Returning caller? Tell Maya you've called before (or click **I've called before**). A
   fake text message with a 6-digit code appears; type it in to verify. Verified callers can
   also **Delete my data**.

The status pill shows **Listening**, **Speaking**, **Needs your OK**, **Booked** or **Not
booked**. **What Maya is doing** shows the circuit: Heard, Understood, Checked calendar,
Waiting for your OK, Booked and saved to CRM.

### Team control room

Sign in at **Team control room**. What each role can do:

| | viewer | agent | admin |
| --- | --- | --- | --- |
| Overview: metrics, queue, guardrails, usage | yes | yes | yes |
| Calls list, evaluation results | yes | yes | yes |
| Open a call (transcript, tools, bookings, audit log) | | yes | yes |
| Take over a call from the queue | | yes | yes |
| Start an evaluation run | | | yes |

Roles are checked by the backend on every request (`backend/app/auth.py`); the UI only hides
what the backend would refuse.

- **Overview.** Calls today, Booked, Need a human and Policy flags come from the database
  and count live calls only (evaluation calls are excluded). The **Needs a human** queue
  lists open handoffs with their reason. **Take over call** marks the handoff taken; if the
  call is still live, the caller sees who took over, Maya says goodbye, and the call ends.
  **Guardrails** lists the rules the backend enforces. **Usage** shows calls against the
  daily cap, estimated cost, audio minutes, and latency p50 and p95 for today.
- **Calls.** Every call, newest first, with outcome chips; filter Live, Evaluation or All.
  A call's page shows its transcript, bookings with each step's status and idempotency key,
  tool calls with arguments and results, handoffs, policy flags, verification attempts,
  usage and the audit log.
- **Evaluation.** Start a scripted or live run and read its results (see below).

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

## Real integrations

`INTEGRATIONS_MODE=mock` (the default) uses the in-process calendar and CRM.
`INTEGRATIONS_MODE=live` uses HubSpot and Google Calendar behind the same interfaces; the
backend refuses to start if any setting below is missing. Use a test HubSpot account and a
dedicated test calendar, with fake details only.

### HubSpot (service key)

HubSpot has replaced new private apps with **service keys**. Both are sent the same way
(`Authorization: Bearer pat-…`), so either works in `HUBSPOT_PRIVATE_APP_TOKEN`.

1. In a HubSpot account where you are a super admin (a free account works), open
   **Development > Keys > Service keys** and click **Create service key**.
   (On the **Legacy apps** page, the **Create a service key** button leads to the same place.)
2. Name it (for example "VoxCircuit test"). Click **Add new scope**, tick
   `crm.objects.contacts.read` and `crm.objects.contacts.write`, click **Update**, then
   **Create**.
3. Open the key, click **Show**, then **Copy**, and paste it into `backend/.env` as
   `HUBSPOT_PRIVATE_APP_TOKEN`. It starts with `pat-`.

HubSpot suggests rotating service keys every six months (**Rotate and expire later**
gives 7 days of overlap).

How it is used (`backend/app/integrations/hubspot.py`):

- Contacts are upserted with email as the id (`POST /crm/v3/objects/contacts/batch/upsert`),
  so a retry lands on the same contact: the step is idempotent without HubSpot keys.
- Only standard properties are written: email, first and last name, ZIP. Reason and
  vehicle stay in VoxCircuit's database.
- "Delete my data" looks the contact up, then GDPR-deletes it
  (`POST /crm/v3/objects/contacts/gdpr-delete`). It never GDPR-deletes an unknown email,
  because HubSpot would blocklist that email.

### Google Calendar (OAuth 2.0)

Use a personal Google account; school or work accounts often block Cloud projects.

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project, then open
   **APIs & Services > Library**, find **Google Calendar API** and click **Enable**. No billing
   is needed.
2. Open **Google Auth platform > Branding** and click **Get started**: enter an app name and
   your email, choose audience **External**, enter a contact email, agree to the policy, and
   click **Create**. Then under **Google Auth platform > Audience**, leave publishing status
   on **Testing** and add your Google account under **Test users**.
3. Under **Google Auth platform > Clients**, click **Create client**, choose **Desktop app**,
   and click **Create**. Put its client ID and secret in `backend/.env` as
   `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`. No JSON file is needed.
4. In Google Calendar, create a new calendar (for example "VoxCircuit test"). In its
   settings under **Integrate calendar**, copy the **Calendar ID** into `GOOGLE_CALENDAR_ID`.
5. Run the one-time sign-in, approve access in the browser, and paste the printed line into
   `backend/.env`:

   ```bash
   cd backend
   python -m app.integrations.google_oauth   # prints GOOGLE_OAUTH_REFRESH_TOKEN=...
   ```

   It uses the desktop-app flow: PKCE and a loopback redirect to `http://127.0.0.1:<port>`.
   It asks for `calendar.events` and `calendar.freebusy` only.
6. Set `INTEGRATIONS_MODE=live` and restart the backend.

While the consent screen is in **Testing**, Google expires refresh tokens after **7 days**.
When that happens, bookings fail with a message saying so; run step 5 again.

How it is used (`backend/app/integrations/google_calendar.py`):

- Availability uses free/busy for the test calendar.
- Each booking step's idempotency key maps to a fixed event id (base32hex, as the API
  requires). The client looks the event up before creating it, so a retry after a lost
  response finds the event instead of creating a second one.
- Rescheduling moves the event; "delete my data" deletes the customer's booked events.

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

## Known limits

- Not yet run against the real services: the Gemini Live API (including how it uses the
  tool schemas), HubSpot and Google Calendar. Their clients are tested against fakes of each
  API; run a live call, a live evaluation, and a booking in live-integrations mode once you
  have credentials.
- The browser was tested in headless Chrome with scripted sessions (and a fake microphone
  for voice), not with Gemini or a real microphone.
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
docker-compose.yml       Postgres, backend, frontend
.github/workflows/ci.yml Lint, type checks and tests
```

## Next steps

1. Run the live checks: a Gemini call, `python -m app.evaluation --mode live`, and a booking
   with `INTEGRATIONS_MODE=live`.
2. Deployment (not started): a host for the backend and frontend, managed Postgres, secrets
   in the platform's secret store, HTTPS for the microphone.
