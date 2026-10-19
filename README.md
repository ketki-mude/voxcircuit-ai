# VoxCircuit AI

**Conversations that turn into actions.**

A real-time voice agent for auto insurance intake. Maya, an AI assistant for the fictional
Northstar Insurance, qualifies leads and sets up consultations with a licensed human agent.
She does not quote prices, recommend coverage, promise approval, or give legal or financial
advice.

> Demo only. Northstar Insurance is fictional. Use fake details only: on the free tier,
> Google may use prompts and audio to improve its products.

## Status

Step 2 of the build: **voice and text calls**. You talk (or type), Maya answers out loud,
and every finalized turn is stored in PostgreSQL as text. Audio is never stored.

| Works now | Not built yet |
| --- | --- |
| Voice calls: 16 kHz mic capture, 24 kHz playback with a jitter buffer, barge-in | Tools, approvals, booking workflow |
| Text calls through the same `LiveSession` path | Returning-customer verification |
| `GeminiLiveSession` (Gemini Live API) and `FakeLiveSession` (offline) | Policy monitor, handoffs |
| Caller and Maya transcripts, usage, latency and append-only audit logs in PostgreSQL | Team control room, calls page, evaluation |
| Caps: call length, turns per call, calls per day, quota errors | Caller tokens and team login |
| Consent banner, enforced by the backend | |

## Architecture

```
Browser (Next.js)  <--JSON + PCM over WebSocket-->  FastAPI  /ws/call  <-->  LiveSession
                                                       |                        |-- GeminiLiveSession -> Gemini Live API
                                                       v                        '-- FakeLiveSession   (scripted, no network)
                                                   PostgreSQL
                                         calls, messages, usage_logs, audit_logs
```

- The Gemini API key lives only in `backend/.env`. The browser never sees it.
- The backend owns the Live session, relays audio both ways, stores transcripts (text only,
  never audio), meters usage, measures latency and enforces the caps.
- `CallHandler` (`backend/app/calls/handler.py`) runs one call. It only sees
  provider-neutral events (`backend/app/live/events.py`), so the fake and the real session
  are interchangeable.

## Prerequisites

- macOS with Homebrew PostgreSQL 16 running: `brew services start postgresql@16`
- Python 3.11 or newer (3.12 is used below)
- Node.js 20.9 or newer (required by Next.js 16)
- Optional: a free Gemini API key. Without one, use the **Offline test** model.

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
cp .env.example .env          # then set GEMINI_API_KEY in .env
alembic upgrade head
uvicorn app.main:app --reload --port 8010
```

Check it: `curl localhost:8010/api/health` should print `{"status":"ok"}`.

The backend runs on port 8010 because port 8000 is often taken by other local projects. If
you change the port, update `NEXT_PUBLIC_BACKEND_WS_URL` in the frontend too.

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open <http://localhost:3000>.

## Using it

1. Tick **I understand** on the consent banner. The backend refuses to start a call without it.
2. Pick a model:
   - **Gemini Live**: the real model. Needs `GEMINI_API_KEY`.
   - **Offline test**: `FakeLiveSession` plays a short scripted intake. No network, no key.
     It only answers typed turns, so it is text-only.
3. Pick a mode:
   - **Voice**: talk through your microphone. The browser asks for microphone access the
     first time. **Use headphones**, otherwise Maya can hear herself through your speakers
     and interrupt her own reply.
   - **Text**: type your replies. Maya still speaks on the server side; you read her
     transcript.
4. Click **Start call**. Maya greets you. In a voice call you can also type.
5. Click **End call**, or let a cap end it.

The status pill shows **Listening** while the microphone is live and **Speaking** while
Maya's audio plays. The waveform shows your microphone while you talk and Maya's voice while
she speaks. **Latency** is the time from your last words to Maya's first audio (see below).

Voice is built on standard Web Audio APIs (AudioWorklet, getUserMedia) and has been tested
in Chrome. It needs `localhost` or `https`: browsers block the microphone on other plain
`http` pages.

## Tests and checks

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
They refuse to run against a database whose name does not end in `_test`.

## Configuration

Backend settings come from environment variables or `backend/.env`
(see `backend/.env.example`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://localhost:5432/voxcircuit` | App database |
| `TEST_DATABASE_URL` | `postgresql+psycopg://localhost:5432/voxcircuit_test` | Used by pytest only |
| `FRONTEND_ORIGINS` | `http://localhost:3000` | Comma-separated origins allowed to open `/ws/call` |
| `GEMINI_API_KEY` | empty | Gemini API key. Empty means only the offline model works |
| `GEMINI_LIVE_MODEL` | `gemini-3.8-live` | Live API model |
| `VAD_SILENCE_DURATION_MS` | `700` | Silence that ends a caller turn (automatic activity detection) |
| `MAX_CALL_SECONDS` | `300` | A call ends after this long |
| `MAX_TURNS` | `30` | Caller turns per call; the call ends after Maya answers the last one |
| `DAILY_CALL_CAP` | `20` | New calls are refused once this many started today (server's local day) |
| `AUDIO_COST_PER_MIN_IN` | `0.005` | USD per minute of caller audio, for estimates |
| `AUDIO_COST_PER_MIN_OUT` | `0.018` | USD per minute of model audio, for estimates |

The cost defaults are the paid-tier list prices for `gemini-3.8-live` as of October 2026
(<https://ai.google.dev/gemini-api/docs/pricing>). The free tier is not billed; the estimate
shows what the same usage would cost on the paid tier.

Frontend: `NEXT_PUBLIC_BACKEND_WS_URL` in `frontend/.env.local` (default
`ws://localhost:8010/ws/call`). It is fixed at build time.

## How a call works

1. In a voice call, the browser first opens the microphone and the audio player (both
   AudioContexts are created inside the Start click, so browsers allow playback).
2. The browser opens `/ws/call` and sends
   `{"type": "start", "engine": "gemini" | "fake", "input_mode": "audio" | "text", "consent": true}`.
3. The backend checks consent and the daily cap, creates the `calls` row, connects the
   Live session, and replies `call_started`. Only then does the browser stream the mic.
4. The backend sends a short kickoff turn so Maya greets the caller first.
5. Caller audio: an AudioWorklet (`public/worklets/pcm-capture.js`) downsamples the mic to
   16 kHz, 16-bit mono and sends 40 ms binary frames. The backend forwards them to the Live
   API (`send_realtime_input`), whose automatic voice activity detection decides when the
   caller has finished. Typed messages are sent as ordered turns (`send_client_content`).
6. Maya's audio comes back as 24 kHz PCM. The backend forwards each chunk as a binary frame,
   and an AudioWorklet player (`public/worklets/pcm-player.js`) plays it through a jitter
   buffer: it waits for 120 ms of audio before starting, re-buffers after an underrun, and
   plays a short tail when the turn ends.
7. Transcripts stream as `user_delta` and `assistant_delta` fragments. The backend saves the
   caller's words as one `messages` row when the transcript is marked finished, when Maya
   starts replying, or at turn end, whichever comes first. Maya's reply is saved at turn
   end. Both are stored with `source = 'transcription'`; typed turns use `'typed'`.
8. **Barge-in**: when the caller talks over Maya, the Live API reports an interruption. The
   backend saves her partial reply with `interrupted = true` and sends `interrupted`, and the
   browser clears its playback queue at once.
9. The call ends when the caller ends it, a cap is hit, the socket closes, or the Live API
   fails. The backend saves any partial turn, records usage and closes the call row. The
   browser stops the microphone and the player.

### Latency

Latency is measured on the backend for each of Maya's replies: from the caller's **last
voiced audio frame** (or the moment a typed turn was sent) to **Maya's first audio chunk**.
A frame counts as voiced when its RMS level is above about -36 dBFS (`SPEECH_RMS_THRESHOLD`
in `backend/app/live/audio.py`).

- By this definition it includes the silence the voice activity detector waits for
  (`VAD_SILENCE_DURATION_MS`, 700 ms by default), because the caller waits through it too.
- It excludes the browser's 120 ms playback buffer and the trip to the browser (tiny on
  localhost).
- It is sent to the browser as soon as Maya's first audio arrives, and stored on her
  message in `messages.latency_ms`. p50 and p95 reporting comes with the control room.

### WebSocket messages

JSON text frames carry the messages below. In a voice call, binary frames carry raw 16-bit
little-endian mono PCM: caller audio at 16 kHz up (at most one second per frame), Maya's
audio at 24 kHz down. Text calls send no binary frames either way.

| Direction | `type` | Fields |
| --- | --- | --- |
| client to server | `start` | `engine`, `input_mode` (`audio` or `text`), `consent` |
| client to server | `user_text` | `text` (1 to 1000 characters), in either mode |
| client to server | `end` | none |
| server to client | `call_started` | `call_id`, `engine`, `max_call_seconds`, `max_turns` |
| server to client | `user_delta` | `text` (unsaved fragment of the caller's speech) |
| server to client | `assistant_delta` | `text` (unsaved fragment of Maya's reply) |
| server to client | `latency` | `latency_ms` (sent at Maya's first audio) |
| server to client | `message` | `id`, `seq`, `role`, `text`, `interrupted`, `latency_ms` (saved turn) |
| server to client | `interrupted` | none |
| server to client | `turn_complete` | `latency_ms` |
| server to client | `usage` | `elapsed_seconds`, `user_turns`, `audio_seconds_in`, `audio_seconds_out`, `estimated_cost_usd` |
| server to client | `call_ended` | `reason`, `message` |
| server to client | `error` | `code` (`invalid_message`, `consent_required`, `not_configured`), `message` |

End reasons: `caller_ended`, `disconnected`, `max_duration`, `max_turns`, `daily_cap`,
`quota`, `error`.

## Gemini Live session details

Checked against google-genai 2.31 and the Live API docs (October 2026):

- **Response modality is always AUDIO.** Native audio models do not support TEXT. Text
  mode reads the model's output transcription and discards the audio. The discarded audio
  is still counted in usage, because it is still generated.
- **Transcription** of input and output audio is on (`input_audio_transcription`,
  `output_audio_transcription`).
- **Voice activity detection** is automatic, with a silence duration set by
  `VAD_SILENCE_DURATION_MS`.
- **Long calls** use context window compression (`sliding_window`).
- **Reconnects are transparent.** The session keeps the latest resumption handle. On a
  `go_away` it reconnects with that handle: immediately when idle, or after the current
  turn. It also reconnects after a dropped connection, up to 3 times in a row. Quota and
  configuration errors are never retried.
- **While it reconnects**, caller audio frames are dropped rather than queued (late audio
  is useless), and a typed turn that hit the dropped connection is sent again once the
  session has resumed. Each go-away and resume is written to `audit_logs`.
- **Quota errors** (HTTP 429, `RESOURCE_EXHAUSTED`) end the call with a friendly message and
  mark it `failed`.

## Data and privacy

- Only text transcripts are stored. Audio is never stored.
- `audit_logs` is append-only. A database trigger rejects every `UPDATE`, `DELETE` and
  `TRUNCATE`. Its payloads hold ids and counts, never message text or personal details.
- Every call records the consent notice the caller accepted (`call.consent` audit event).
- `/ws/call` rejects browser origins not listed in `FRONTEND_ORIGINS`. Caller session
  tokens arrive with the auth step.

## Known limits

- `GeminiLiveSession` is tested against a fake SDK client: config, message mapping, error
  mapping, go-away, resumption and sends during a reconnect. It has not yet been run
  against the live API from this repo. Run one Gemini Live call after adding your key.
- The browser audio pipeline was tested in headless Chrome with a fake microphone and a
  scripted session, not with Gemini or a real microphone.
- Without headphones, browser echo cancellation may not remove Maya's voice from the mic,
  so she can interrupt herself.
- Background noise above the speech threshold makes the latency reading look shorter than
  it was.
- If transcription of the caller's words lags behind Maya's reply, the late words are
  saved as a separate caller message.
- The offline test engine is text-only.
- Calls are anonymous for now: no caller tokens and no team login yet.

## Project layout

```
backend/
  app/
    main.py              FastAPI app factory
    config.py            Settings (env / .env)
    models.py            SQLAlchemy models
    prompts.py           Maya's system instruction
    api/                 /ws/call and /api/health
    calls/               Call handler, WebSocket protocol, recorder, usage meter, latency
    live/                LiveSession interface, Gemini and fake sessions, events, PCM helpers
  alembic/               Migrations
  tests/
frontend/
  public/worklets/       AudioWorklets: mic capture (16 kHz) and jitter-buffered player
  src/app/               Next.js App Router page and layout
  src/components/call/   Call console, consent banner, transcript, status pill, waveform
  src/hooks/use-call.ts  WebSocket call state, microphone and player
  src/lib/audio/         MicCapture and PcmPlayer wrappers around the worklets
  src/lib/               Protocol types, config
```

## Next steps

1. Tools with Pydantic schemas, approvals, and the two-system booking workflow with
   idempotency (mock calendar and CRM first).
2. Returning-customer verification, handoffs, and the policy monitor.
3. Team control room (with latency p50 and p95), calls page, evaluation runs, Docker
   Compose and GitHub Actions.
