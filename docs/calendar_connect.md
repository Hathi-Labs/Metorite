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

You can turn off sending your tasks in the row of the connection. Turning it
off deletes nothing in Google or Outlook.

## Members

### Google (personal Gmail or Workspace)

1. Open **Calendar**, then **Settings**, then **Connect Google Calendar**.
   You go to a Google page.
2. Choose your Google account. Google shows the permission page.
3. Read the list of permissions. Keep every box on. Press **Continue**.
4. You return to **Calendar**. The row of your account shows "Syncing…".
5. Wait for "Syncing…" to end. Your events appear.

What each permission means:

- **See your calendar events.** Metorite reads your events. It cannot change them.
- **Make its own calendar.** Metorite makes a calendar called **Metorite**.
  It writes your timed tasks there, and only there. It never writes to
  your other calendars.
- **Your email address.** Metorite shows the address of the account that you connect.

Your Google may show "Google hasn't verified this app". This happens
while the review of Google runs. Press **Advanced**, then **Go to
Metorite**.

If your company uses Workspace, your admin can block the connect. You then
see "Access blocked" or "Admin approval needed". Send your admin the admin
steps below.

### Outlook (personal) and Microsoft 365 (work)

1. Open **Calendar**, then **Settings**, then **Connect Outlook**.
   You go to a Microsoft page.
2. Choose your Microsoft account. Microsoft shows the permission page.
3. Press **Accept**.
4. You return to **Calendar**. The row of your account shows "Syncing…".
5. Wait for "Syncing…" to end. Your events appear.

The permissions are **Calendars (read and write)**, sign-in, your email
address, and "keep access". Metorite asks for no email permission. If you
connected Outlook mail before, Microsoft also lists the mail items that you
gave to Metorite then. That is normal.

A work account can show "Need admin approval". Send your admin the note
and the steps below. A personal account (outlook.com, hotmail.com,
live.com) needs no admin.

### Already connected your email?

1. Open **Calendar**, then **Settings**.
2. Find the row that says your mailbox is connected, and press **Sync its calendar**.
3. The provider asks you to allow calendar access for the same account.
4. A Microsoft 365 work account may need an admin to approve Metorite once.
5. Mail and calendar stay separate. Disconnect one and the other stays connected.

You find the same button under your accounts in **Email**.

### Which calendar Metorite reads

Metorite reads your main calendar only. For Google that is your main
calendar, which carries your own address. For Outlook that is your default
calendar. It does not read shared calendars or the calendars of colleagues.

To hide the **Metorite** calendar in Google, tick it off in the list of
calendars at the left of Google Calendar. In Outlook, tick it off in the
calendar list. Hiding it deletes nothing, and the sync goes on.

## Admins

### Google Workspace

1. Open the Google Admin console, then **Security**, then **Access and data
   control**, then **API controls**.
2. Press **Manage third-party app access**.
3. Press **Add app**, then **OAuth App Name Or Client ID**.
4. Enter the Metorite client ID. Metorite support gives it to you.
5. Choose the scope: all users, or one organizational unit.
6. Choose **Trusted**. Press **Finish**.

### Microsoft 365 (Entra)

A member with a work or school account can send you this note:

> Please approve the Metorite app for these delegated permissions:
> Calendars.ReadWrite, User.Read, offline_access.

Do one of these:

- **In the admin center.** Open the Entra admin center, then **Enterprise
  applications**, then **Metorite**, then **Permissions**. Press **Grant
  admin consent for** your company.
- **With the link.** Open
  `https://login.microsoftonline.com/<your tenant>/adminconsent?client_id=<Metorite client ID>`.
  Sign in as a Global Administrator or Privileged Role Administrator.
  Press **Accept**.

Then tell the member to press **Connect Outlook** again. It now works with
no second step. The consent covers the whole company once, so other members
need no approval.

If **Metorite** is not in the list of Enterprise applications, use the link
first. The link adds the app to your company.

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

The screen shows one of these lines. Find the one that starts like yours, and do what the last column says.

| You see | Cause | What to do |
|---|---|---|
| "The sign-in was cancelled or blocked." | You closed the page, or your admin blocks the app | Press **Connect** again. If it repeats, send the admin steps to your admin. |
| "Access blocked" (Google) or "Need admin approval" (Microsoft) | Your company blocks third-party apps | Send the admin steps to your admin. Press **Connect** again when the admin says it is done. |
| "does not let Metorite read this calendar. Ask your administrator to allow Metorite." | The admin allowed the app for other people, not for you | Send the admin steps to your admin. |
| "The consent did not include calendar access." | You cleared a box on the permission page | Press **Connect** and keep every box on. |
| "gave Metorite more access than it asks for" | The account gave Metorite extra permissions earlier | Remove Metorite in the security page of your Google or Microsoft account. Then connect again. |
| "did not send a long-lived sign-in" | The provider kept back the token that lets Metorite sync | Remove Metorite in the security page of your account. Then connect again. |
| "needs you to sign in again. Press Reconnect." | The token ended: you changed your password, or you removed the access | Press **Reconnect**. |
| "Metorite could not reach Google" or "Microsoft" | The provider did not answer | Wait. Metorite tries again in a minute. |
| "asked Metorite to slow down" | The provider limits how fast Metorite may read | Wait a few minutes. The sync goes on by itself. |
| "had a problem. The next sync tries again in a minute." | The provider had a fault | Wait a minute. |
| "This calendar is too large to read in one pass." | The calendar holds a very large number of events | Press **Sync now**. If it repeats, contact support. |
| "Metorite could not reach the 'Metorite' calendar." | Metorite could not find or make the calendar, or could not save an event. A new account with no calendar yet is one cause. | Press **Sync now**. If it repeats, press **Reconnect**. If your account is new, open Google Calendar or Outlook once first. |
| "Metorite could not read your" ... "account address" | The provider did not send your address | Press **Connect** again. |
| "This calendar is already connected. Reload the page." | Another page of yours connected it first | Reload the page. |
| "That mailbox is not connected to your account. Reload the page." | You disconnected the mailbox in Email | Reload the page. Connect the mailbox again in **Email**. |
| "could not reach Google" or "Microsoft" ... "safely" | Metorite refused the address of the provider as unsafe | Try again later. If it stays, tell your admin. |
| "no longer accepts the sign-in. Press Reconnect." | The provider ended the sign-in | Press **Reconnect**. |
| "Connecting did not work. Try again." | A fault with no more detail | Press **Connect** again. If it repeats, contact support. |
| "The sync did not work. Try again in a minute." | A fault with no more detail | Press **Sync now** in a minute. |
| "Disconnect did not work. Try again." | A fault with no more detail | Press **Disconnect** again. |
| "The request did not go through. Check your connection and try again." | Your network, or Metorite, did not answer | Check your connection. Try again. |
| No events after 5 minutes | The sync is not on for your company yet | Ask your admin. |
| A task shows twice | You made the event by hand in your calendar as well | Delete the copy in your calendar. |

## Hide, disconnect and remove

- **Hide the Metorite calendar** in Google or Outlook: it only hides the
  calendar. Metorite keeps the events there and the sync goes on.
- **Turn off sending tasks** in the row of the connection. Metorite stops
  writing. It deletes nothing in Google or Outlook.
- **Disconnect** in Metorite. Metorite removes the events that it read into
  Metorite. Your own events and your tasks stay. It does not sign you out
  of Google or Microsoft, and it does not delete the **Metorite** calendar
  there.
- **Remove the access fully.** Remove Metorite in your Google account
  security page, or at `myapps.microsoft.com` for Microsoft.
