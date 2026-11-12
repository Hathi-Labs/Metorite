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

### Already connected your email?

1. Open **Calendar**, then **Settings**.
2. Press **Sync its calendar** on the row of your mailbox.
3. The provider asks you to allow calendar access for the same account.
4. A Microsoft 365 work account may need an admin to approve Metorite once.
5. Mail and calendar stay separate. Disconnect one and the other stays connected.

You find the same button under your accounts in **Email**.

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
