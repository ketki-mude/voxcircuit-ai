# VoxCircuit AI

**A voice assistant that books insurance consultations safely.**

You talk to Maya, an AI assistant for **Northstar Insurance** (a made-up company). She
collects your details, finds a free time, and books a call with a licensed human agent,
but only after you press **Approve**. A team dashboard shows every call, flags mistakes,
and lets a person take over.

> This is a demo. Northstar Insurance is fictional. **Use fake names, emails and details
> only.** The free Gemini tier may use conversations to improve Google's products.

---

## The problem

Insurance agencies get many calls that start the same way: *"I want a quote," "I'm
switching insurers," "I bought a new car."* The first few minutes are routine (name,
email, car, ZIP code, a time to talk to an agent), but staff still spend hours on them,
and callers who ring after hours reach voicemail.

An AI voice assistant could answer every call instantly, but in insurance a careless one
is dangerous. It might:

- quote a price the company can't honor, or promise that a policy will be approved
- give coverage advice that only a licensed agent may give
- book an appointment the caller never agreed to, or say "you're booked" when nothing was
- tell one caller about another person's account
- keep going when the caller is upset or has a claim that needs a human

## The solution

VoxCircuit lets the AI do the talking and keeps the important decisions in ordinary code
that always follows the rules.

| Risk | What VoxCircuit does |
| --- | --- |
| Booking without consent | Maya can only *ask* to book. The caller sees a **"Review before I book"** ticket, and nothing happens until they press **Approve**. |
| Saying "you're booked" when it failed | The server makes the booking and tells Maya the real result. If something failed, she says so and a human follows up. |
| Quoting prices or giving advice | A **policy monitor** checks everything Maya says. If she slips, the call is flagged and she corrects herself right away. |
| Sharing someone else's data | Returning callers must type a 6-digit code before Maya sees anything about their past visit, and she only ever sees their own. |
| Situations that need a person | Claims, complaints, upset callers, and requests for a human go to a **"Needs a human"** queue, where a team member can take over. |
| Proving it works | **30 test conversations** run automatically and measure success, mistakes, and data leaks. |

Privacy: only text transcripts are stored, never audio. Every important action goes into an
audit log that can't be edited.

---

## What it looks like

**1. Start a call.** Tick the consent box, choose voice or text, and press Start call.

![Customer view before a call](docs/images/1-customer-start.png)

**2. Approve the booking.** Maya puts a ticket on screen. "What Maya is doing" shows each
step as it happens.

![Booking ticket waiting for approval](docs/images/2-customer-approval.png)

**3. Booked.** The booking is saved to the CRM and the calendar, and Maya confirms it.

![Booking confirmed](docs/images/3-customer-booked.png)

**4. Team control room.** Today's numbers, the "Needs a human" queue with **Take over
call**, the rules the system enforces, and usage against the daily limit.

![Control room overview](docs/images/4-control-room.png)

**5. Call details.** The full transcript, the booking's steps, and any policy flags. Here
Maya quoted a price, was flagged, and corrected herself.

![Call detail with a policy flag](docs/images/5-call-detail.png)

**6. Evaluation.** 30 scripted conversations, with the results that must stay at zero:
unauthorized writes and history leaks.

![Evaluation results](docs/images/6-evaluation.png)

---

## How it works

### The big picture

```mermaid
flowchart TB
    caller["Caller<br/>web page with mic and speaker"]
    team["Team member<br/>control room"]

    subgraph server["VoxCircuit server"]
        direction LR
        handler["Call handler<br/>relays voice, saves transcripts"]
        rules["Rules in code<br/>approvals, policy monitor,<br/>handoffs, verification"]
        booking["Booking workflow<br/>CRM first, then calendar"]
        handler --> rules --> booking
    end

    ai["Gemini Live<br/>Maya's voice and understanding"]
    db[("Database<br/>transcripts, bookings, audit log")]
    crm["HubSpot CRM<br/>or practice version"]
    cal["Google Calendar<br/>or practice version"]

    caller <-->|voice or text| handler
    team <-->|numbers, queue, take over| server
    handler <-->|voice and requests| ai
    handler --> db
    booking --> crm
    booking --> cal
```

- The **web page** only records your voice and plays Maya's. It never sees any secret keys.
- The **server** sits in the middle of every call. Maya can only *request* actions
  ("check the calendar", "book this time"); the server checks each request and carries it
  out, or refuses it.
- **Gemini Live** is Google's real-time voice AI. It hears the caller and speaks as Maya.
- The **CRM** (HubSpot) holds customer contacts, and the **calendar** (Google Calendar)
  holds the appointments. Practice versions built into the server are used until real
  accounts are connected.

### One booking, step by step

```mermaid
sequenceDiagram
    actor Caller
    participant Page as Web page
    participant Server as VoxCircuit server
    participant Maya as Maya (Gemini Live)
    participant Systems as CRM and calendar

    Caller->>Maya: "I'd like to switch my car insurance"
    Maya->>Caller: asks for name, email, car and ZIP code
    Maya->>Server: request: check free times
    Server->>Systems: which times are free?
    Systems-->>Server: free times
    Server-->>Maya: free times
    Maya->>Caller: "I have Thursday at 10 or 2. Which works?"
    Caller->>Maya: "Thursday at 10"
    Maya->>Server: request: book Thursday at 10
    Server->>Page: shows the "Review before I book" ticket
    Note over Server: Nothing is booked yet
    Caller->>Page: presses Approve
    Server->>Systems: save the contact, then add the event
    Systems-->>Server: both done
    Server-->>Maya: real result: booked
    Maya->>Caller: "You're booked for Thursday at 10"
```

Everything Maya says passes through the server, which saves it as text and checks it for
prices, advice, approval promises, and false booking claims.

### Where things live in the code

| What happens | Where |
| --- | --- |
| Mic capture and audio playback in the browser | `frontend/src/hooks/use-call.ts`, `frontend/public/worklets/` |
| Customer screen (ticket, timeline, transcript) | `frontend/src/components/call/` |
| Control room screens | `frontend/src/components/team/` |
| The call connection | `backend/app/api/ws_call.py` |
| Running one call: relaying voice, transcripts, notices | `backend/app/calls/handler.py` |
| Talking to Gemini Live | `backend/app/live/gemini.py` |
| Checking and running Maya's requests (tools) | `backend/app/tools/` |
| Approval tickets and the booking workflow | `backend/app/booking/` |
| Policy monitor and handoff rules | `backend/app/safety/` |
| Returning-customer codes | `backend/app/verification/` |
| HubSpot, Google Calendar and practice versions | `backend/app/integrations/` |
| Test conversations and scoring | `backend/app/evaluation/` |

---

## Run it

### One-time setup (macOS)

You need Homebrew. Install PostgreSQL, Python and Node.js, then start the database:

```bash
brew install postgresql@16 python@3.12 node
brew services start postgresql@16
createdb voxcircuit
createdb voxcircuit_test
```

If `createdb` is not found, add PostgreSQL to your path and try again:
`echo 'export PATH="$(brew --prefix postgresql@16)/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc`

Set up the server (backend). Run these from the project folder (`voxcircuit-ai`):

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # then open .env and add your GEMINI_API_KEY
alembic upgrade head
python -m app.cli create-user --email you@example.com --name "Your Name" --role admin
```

The last command creates your control-room login. It asks for a password of at least 12
characters.

Set up the web app (frontend), again from the project folder:

```bash
cd frontend
npm install
cp .env.example .env.local
```

A free Gemini API key comes from <https://aistudio.google.com/apikey> (no billing needed).
Without one, only the **Offline test** model works.

### Every time

Open two terminal windows and go to the project folder in each:

```bash
# Terminal 1: the server
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8010
```

```bash
# Terminal 2: the web app
cd frontend
npm run dev
```

Then open <http://localhost:3000>. Use **Team control room** at the top right to sign in
to the dashboard.

### Or with Docker

```bash
cp .env.example .env           # set POSTGRES_PASSWORD
docker compose up --build
docker compose exec backend python -m app.cli create-user \
    --email you@example.com --name "Your Name" --role admin
```

Stop the two terminal servers first, because Docker uses the same ports (3000 and 8010).

### Run the tests

```bash
# Server tests, style and type checks (from backend/, with .venv active)
pytest
ruff check . && ruff format --check .
mypy app tests

# The 30 test conversations (no network), then 5 against the real Gemini model
python -m app.evaluation
python -m app.evaluation --mode live

# Web app checks (from frontend/)
npm run lint
npm run typecheck
npm run build
```

`--mode live` uses 5 of the 20 calls allowed per day. GitHub runs the same checks on every
push (`.github/workflows/ci.yml`).

---

## Things to try

Choose **Gemini Live** for a real conversation. **Offline test** only plays a short scripted
intake with no network. Use **headphones** for voice calls, or Maya may hear herself and
stop talking.

| Say this | What should happen |
| --- | --- |
| "Hi, I'm Jordan Lee, jordan.lee@example.com. I want to switch my car insurance. I drive a 2020 Toyota Corolla, ZIP 02139." | Maya checks the calendar and offers times. Pick one, then press **Approve** on the ticket. The status turns **Booked**. |
| Pick a time, then "Actually, can we do the afternoon instead?" | A new ticket replaces the old one. Only the one you approve is booked. |
| Press **Cancel** on the ticket | Nothing is booked, and Maya says so. |
| "How much will this cost me?" | Maya says a licensed agent will give the price. Ask again and the call goes to **Needs a human**. |
| "Should I get full coverage?" | Maya won't advise; an agent will. |
| "Are you a real person?" | Maya says she is an AI assistant. |
| "I was in an accident and need to file a claim." | The call is handed to a human. It appears in the control room queue. |
| "Can I talk to a real person?" | Handoff. In the control room, press **Take over call**: the caller is told an agent has taken over, and the call ends. |
| "My Social Security number is 123-45-6789." | Maya won't take it. Sensitive numbers are refused. |
| In a **new call** after booking: click **I've called before** and enter the same email | A fake text message with a 6-digit code appears. Type it in, and Maya greets you and knows your last appointment. Then try "Can you move my appointment to Friday?" |
| After verifying, click **Delete my data** | Your stored details, bookings and transcripts are deleted. |
| Start talking while Maya is speaking (voice) | She stops and listens. |

Then open **Team control room**, look at the numbers and the queue, open a call to read
its transcript, and run an evaluation from the **Evaluation** tab.

If the server has `INTEGRATIONS_MODE=live`, approved bookings appear as a contact in
HubSpot and an event on your Google test calendar. Otherwise they go to the practice
versions, which you can see at <http://localhost:8010/mock/calendar/events> and
<http://localhost:8010/mock/crm/contacts>.

---

## Team roles

| | Viewer | Agent | Admin |
| --- | --- | --- | --- |
| See numbers, queue, calls list, evaluation results | yes | yes | yes |
| Read a call's transcript, take over a call | | yes | yes |
| Start an evaluation | | | yes |

The server checks the role on every request; the screens just hide buttons you can't use.

## Limits worth knowing

- A full voice call with a real microphone hasn't been tested by the developers yet. Text
  calls, the live evaluation, and the HubSpot and Google connections were checked against
  the real services.
- The policy monitor and handoff rules match words and patterns, so they can miss unusual
  phrasing.
- The verification code is shown on screen because this is a demo. A real system would send
  it to the customer's phone.
- **Take over call** ends the AI call and records who took it; it doesn't connect a person
  into the audio.
- Up to 20 calls a day and 5 minutes per call, to stay within the free tier.
- Not deployed: it runs on your own computer.

## More detail

- [docs/integrations.md](docs/integrations.md): connect a real HubSpot account and Google Calendar
- [docs/reference.md](docs/reference.md): all settings, the API, how calls and bookings
  work internally, evaluation details, privacy, and known limits
