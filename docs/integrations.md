# Connecting HubSpot and Google Calendar

VoxCircuit runs with built-in practice versions ("mocks") of the calendar and CRM by
default. These steps connect a real HubSpot account and a real Google Calendar instead.
Everything here is free, and nothing needs billing.

`INTEGRATIONS_MODE=mock` (the default) uses the in-process calendar and CRM.
`INTEGRATIONS_MODE=live` uses HubSpot and Google Calendar behind the same interfaces; the
backend refuses to start if any setting below is missing. Use a test HubSpot account and a
dedicated test calendar, with fake details only.

## HubSpot (service key)

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

## Google Calendar (OAuth 2.0)

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
