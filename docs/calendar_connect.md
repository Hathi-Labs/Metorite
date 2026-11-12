# Connect your calendar to Metorite

**For:** members and admins. **Time:** about 2 minutes. **Spec:**
`project-docs/specs/calendar_events_reminders.md` §8.1a (CE-14).

Metorite reads your Google or Outlook calendar. Metorite can also write
your scheduled tasks to it. You press one button. You do not make an app
or a key.

## What syncs, and which way

| Item | Direction |
|---|---|
| Events in Google or Outlook | Into Metorite. You see them on your Calendar. |
| A task that you schedule in Metorite | Out to a calendar called **Metorite**, as one event. Your main calendar stays clean. |
| A move or title change of that event in your calendar | Back into Metorite. The newer change wins. |
| A delete of that event in your calendar | The task block goes. The task stays. |
| An event that you make in Metorite with "Also in Google Calendar" | Out to your calendar. |

Metorite never deletes a task because of a change in your calendar. A
sync takes about one minute.

## Members

### Google (personal Gmail or Workspace)

1. Open **Calendar**, then **Settings**, then **Connect Google Calendar**.
2. Choose your Google account.
3. Read the list of permissions. Press **Continue**. Keep every box on.
4. Wait for "Syncing…" to end. Your events appear.

Your Google may show "Google hasn't verified this app". This happens
while the review of Google runs. Press **Advanced**, then **Go to
Metorite**.

If your company uses Workspace, your admin can block the connect. You then
see "Access blocked" or "Admin approval needed". Send your admin the admin
steps below.

### Outlook (personal) and Microsoft 365 (work)

1. Open **Calendar**, then **Settings**, then **Connect Outlook**.
2. Choose your Microsoft account.
3. Press **Accept** on the permission page.
4. Wait for "Syncing…" to end.

A work account can show "Need admin approval". Send your admin the admin
steps below.

### Choose what syncs

Metorite reads every calendar that you own. To stop one, open **Calendar**,
then **Settings**, then your connected account. Untick it. To hide the
**Metorite** calendar, use the eye in Google or Outlook.

## Admins

### Google Workspace

1. Open the Admin console, then **Security**, then **Access and data
   control**, then **API controls**.
2. Press **Manage third-party app access**.
3. Press **Add app**, then **OAuth App Name Or Client ID**.
4. Enter the Metorite client ID. Metorite support gives it to you.
5. Choose the scope: all users, or one organizational unit.
6. Choose **Trusted**. Press **Finish**.

### Microsoft 365 (Entra)

1. Open the Entra admin center, then **Enterprise applications**, then
   **Consent and permissions**.
2. If users may not consent, open the link
   `https://login.microsoftonline.com/<your tenant>/adminconsent?client_id=<Metorite client ID>`.
3. Sign in as a Global Administrator or Privileged Role Administrator.
4. Press **Accept**. The permissions are `Calendars.ReadWrite`,
   `offline_access` and `User.Read`.

Every member of the tenant can then connect with no more approval.

## Operators: the live check before the write flag

Two facts about the write-out cannot be proved with recorded replies. Check
them once with a real account, before you turn on `CALENDAR_SYNC_WRITE`:

- **Google.** A create with a client id that a deleted event holds answers
  HTTP 409. A PATCH with `status: confirmed` brings that cancelled event
  back. A plain PATCH must not.
- **Outlook.** A repeat create with the same `transactionId` answers with the
  event that the first create made. No second event exists.

Use a test account that has a connected calendar and a calendar called
**Metorite**. Run one task pass with the write flag on for a test
organization first, so that the calendar exists. The script makes no
calendar.

```
uv run python scripts/calendar_live_check.py --live --org <org id> --connection <connection id>
```

The script does these things, and nothing else:

- It runs only with `--live`, `--org` and `--connection`. Without them, it
  prints why and stops.
- It never turns a flag on and never prints a token. It may refresh the
  access token and store it, as the sync does.
- It writes events only into that **Metorite** calendar. Each event has no
  attendees, so the provider sends no mail. Each event is free time, two
  days ahead, and is deleted at the end, also when a check fails.

It prints one table with a result for each check:

| Result | Meaning |
|---|---|
| `PASS` | The provider did what the writer expects. |
| `FAIL` | The provider did something else. Do not turn the flag on for that provider. Write the line on task 3036. |
| `info` | A fact with no verdict. Read it, and note it on task 3036. |

The exit code is 0 when every check passes, 1 when a check fails, and 2
when the script refuses or cannot start.

## Troubleshooting

| You see | Cause | What to do |
|---|---|---|
| "Access blocked" (Google) | The admin blocks third-party apps | Send the admin steps to your admin |
| "Need admin approval" (Microsoft) | The tenant needs admin consent | Send the admin steps to your admin |
| "Reconnect" on the account | The token ended: you changed your password, or you removed the access | Press **Reconnect** |
| "Permission missing" | You cleared a box on the consent page | Press **Connect** again. Keep every box on. |
| No events after 5 minutes | The sync is not on for your company yet, or the calendar is not chosen | Check the chosen calendars. Ask your admin. |
| A task shows twice | You made the event by hand in your calendar as well | Delete the copy in your calendar |

To stop the sync, press **Disconnect**. Metorite removes the events it
read. Your own events and tasks stay. To remove the access fully, also
remove Metorite in your Google or Microsoft account security page.
