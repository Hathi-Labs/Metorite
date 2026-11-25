# Set up the Tally connector

> **DRAFT (TLY-2c, task 3969). Do not give this page to a customer yet.**
> Each `[CAPTURE n]` marks a screenshot that does not exist yet.
> Step 7 waits for the Metorite side of the link (LNK-1 to LNK-4).

This page shows how to connect the books in TallyPrime to Metorite. It takes
about 15 minutes. You need a person who can sign in to Windows as an
administrator, and an admin of your company in Metorite.

The connector calls out to Metorite over HTTPS. You open no port, you change
no router, and you send no password to anyone.

## 1. Pick the machine

Use the computer that runs TallyPrime with your company data.

- Install the connector on that computer, and not on another one.
- Keep the computer on during office hours. Metorite can read the books only
  while it is on.

## 2. Turn on the Tally connection

TallyPrime must answer on port 9000 of this computer.

1. Open TallyPrime.
2. Press **F1 (Help)**, then open **Settings**, then **Connectivity**, then
   **Client/Server Configuration**.
3. Set **TallyPrime acts as** to **Server** or **Both**.
4. Set the **Port** to **9000**.
5. Save, then restart TallyPrime.

`[CAPTURE 1: the Client/Server Configuration screen, with Server and port 9000]`

The connector also blocks port 9000 from the network, so only this computer
reaches it.

## 3. Install

1. Run `TallyBridgeSetup.exe` as an administrator.
2. When the install ends, keep **Open the setup page** ticked, and click
   **Finish**. The setup page opens in your browser.
3. The page looks for TallyPrime. Make sure that it says **TallyPrime found**.
4. Tick the companies that Metorite may read, then click
   **Save the companies**.

`[CAPTURE 2: the setup page, with TallyPrime found and the company list]`

Metorite can read only the companies that you tick.

## 4. Pair

1. In **Link to Metorite**, type your company domain, such as `example.com`.
2. Click **Pair this computer**.
3. The page shows a code, such as `WDJB-MJHT`. Send this code to an admin of
   your company in Metorite.

`[CAPTURE 3: the link step, with a code]`

The code ends after 10 minutes. If it ends, click **Pair this computer**
again for a new code.

## 5. Approve

The Metorite admin does these steps:

1. Open **Integrations** in Metorite.
2. Type the code.
3. Check the computer name and the domain on the screen.
4. Read the data notice, then approve.

`[CAPTURE 4: the approval screen in Metorite. It waits for LNK-1 and LNK-2]`

Then go back to the setup page. It shows the name of the company that
approved:

1. Make sure that this is your company.
2. Type its name exactly as the page shows it, then click **Confirm**.
3. If you do not know the company, click **This is not my company**. The
   connector then deletes the pairing.

`[CAPTURE 5: the confirm step, with the company name]`

The connector sends no data before you confirm.

## 6. Keep it running

The connector starts with Windows, before anyone signs in. TallyPrime does
not, and Metorite can read the books only while TallyPrime is open.

1. An administrator sets the path of TallyPrime once, in the settings file:
   `TALLY_EXE`, such as `C:\Program Files\TallyPrime\tally.exe`. The
   setup page shows this path. It cannot change it, because the sign-in
   task runs that program for each person who signs in.
2. On the setup page, in **Keep it running**, tick **Start TallyPrime when a
   person signs in**. TallyPrime then opens with your companies at each
   sign-in.
3. Tick **Keep this computer awake while the bridge runs**. Windows then does
   not sleep while the connector runs.
4. Click **Save these switches**.

`[CAPTURE 6: the Keep it running section, with the path and the two switches]`

## 7. Check

> DRAFT: this step waits for the Metorite side of the link (LNK-1 to LNK-4).

1. In Metorite, open **Integrations**. The connector shows **Connected**.
2. In the chat, ask one question for a test, such as the balance of one ledger.
3. Compare the answer with TallyPrime.

`[CAPTURE 7: Integrations, with the connector Connected]`

## 8. Fix a problem

| What Metorite shows | What it means | What to do |
|---|---|---|
| "TallyPrime is closed on OFFICE-PC" | The connector runs, but nobody has TallyPrime open | Open TallyPrime with your companies. Turn on **Start TallyPrime at sign-in** (step 6) |
| "The program on OFFICE-PC has not called for 12 minutes" | The computer is off or asleep, or it has no internet | Turn the computer on and wake it. Turn on **Keep the computer awake** (step 6) |
| "Waiting for OFFICE-PC" | The admin approved, and nobody confirmed the company name | Open the setup page on that computer, then confirm (step 5) |
| "Pair this machine again" | The pairing ended, or an admin disconnected it | Pair again (step 4) |
| The answer is about the wrong company | Somebody ticked the wrong company | Open the setup page, tick the right companies, then save (step 3) |

## 9. Disconnect

You can disconnect in two places. Each one stops the chat tools at once.

- **In Metorite:** an admin opens **Integrations** and clicks **Disconnect**.
- **On this computer:** open the setup page and click **Unlink this
  computer**.

To remove the connector, uninstall **Tally Bridge** in **Settings**, then
**Apps**. The uninstall also deletes the link and the local copy of the
books.
