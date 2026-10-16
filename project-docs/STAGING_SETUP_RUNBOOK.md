# Staging setup runbook — dev.metorite.com, for a browser session on the owner's PC

**Status:** ACTIVE, 2026-10-16. **Owner:** vjvarada. **Board row:** WS-38.
**Spec:** `specs/staging_and_daily_release.md` §5.10 and §5.11. **Queue entry:** HANDOFF H-335.

**What this does.** It turns the dev box (`srv1747539`, `187.127.179.143`) into the
staging site, `dev.metorite.com`. All the code is on `main`. Four steps need an
owner account, so a person or a browser session on the owner's PC does them.

**Who does what.**

| Part | Who | Where |
|---|---|---|
| A. DNS records | the PC session | Hostinger hPanel |
| B. Google sign-in client | the PC session | Google Cloud Console |
| C. Microsoft sign-in app | the PC session | Microsoft Entra admin center |
| D. AI Router key | the PC session | the Operator Console |
| E. The setup run on the dev box | the PC session | Hostinger hPanel, browser terminal |
| F. The production side, and the check | the agent on the dev box | it reads the result of part E |

Do the parts in this order. Part E needs the values that parts B, C and D give.

---

## 0. Rules for the session that runs this

1. **Keep each secret in one place.** That place is the file in part E. Do not
   paste a secret into a chat or a ticket. Do not put one in a commit or a
   screenshot.
2. **Copy a secret when the page shows it.** Entra and the Operator Console show a
   new secret ONE time. If you lose it, make a new one and delete the old one.
3. **Touch nothing else.** Do not change the production records, the production
   sign-in clients, or another customer in the Operator Console.
4. **Stop and ask the owner** when a page differs from this runbook. Ask when the
   difference changes what a click does.
5. **Write down the five values** of the table below as you get them. Part E needs
   all five.

| Name | Comes from |
|---|---|
| `AUTH_GOOGLE_ID` | part B |
| `AUTH_GOOGLE_SECRET` | part B |
| `AUTH_MICROSOFT_ENTRA_ID_ID` | part C |
| `AUTH_MICROSOFT_ENTRA_ID_SECRET` | part C |
| `CUSTOMER_CONSOLE_ORG_KEY` | part D |

---

## A. DNS records (Hostinger hPanel)

The domain `metorite.com` uses Hostinger DNS (nameservers `*.dns-parking.com`). A
wildcard record sends every unknown name to the production box today, so
`dev.metorite.com` reaches production until you do this part.

1. Open `https://hpanel.hostinger.com` and sign in.
2. Open **Domains**, then `metorite.com`, then **DNS / Nameservers**.
3. Add a record:
   - **Type:** `A`
   - **Name:** `dev`
   - **Points to:** `187.127.179.143`
   - **TTL:** `300`
4. Add a second record:
   - **Type:** `A`
   - **Name:** `api.dev`
   - **Points to:** `187.127.179.143`
   - **TTL:** `300`
5. Do not change or delete another record. The `*` record, `app`, `api`, `mcp` and
   `operator` stay as they are.

**Check.** After about 5 minutes, on the PC, run:

```bash
nslookup dev.metorite.com
nslookup api.dev.metorite.com
```

Each must answer `187.127.179.143`. If one answers `187.127.172.200`, the record
is not active yet. Wait, and run the check again.

---

## B. Google sign-in client (Google Cloud Console)

Use the Google Cloud project that holds the PRODUCTION sign-in client. Its client ID
starts with `769825289659-`. The number `769825289659` is that project's number.

1. Open `https://console.cloud.google.com/apis/credentials`.
2. In the project picker, choose the project whose **Project number** is
   `769825289659`. The project picker shows the number for each project.
3. Click **Create credentials**, then **OAuth client ID**.
4. **Application type:** `Web application`.
5. **Name:** `Metorite Staging (dev.metorite.com)`.
6. **Authorized JavaScript origins:** add `https://dev.metorite.com`.
7. **Authorized redirect URIs:** add exactly this, with no slash at the end:

   ```
   https://dev.metorite.com/api/auth/callback/google
   ```

8. Click **Create**.
9. Copy the **Client ID**. That is `AUTH_GOOGLE_ID`. It ends with
   `.apps.googleusercontent.com`.
10. Copy the **Client secret**. That is `AUTH_GOOGLE_SECRET`.

Do not edit the production client, and do not edit the OAuth consent screen. The
new client uses the consent screen of the project as it is.

**Check.** The credentials list shows two web clients: the production one and
`Metorite Staging (dev.metorite.com)`.

---

## C. Microsoft sign-in app (Microsoft Entra admin center)

Use the Entra tenant that holds the PRODUCTION sign-in app. Its Application
(client) ID is `3bfeff54-14fb-4cee-8b17-2c8d41cad6a8`.

1. Open `https://entra.microsoft.com`.
2. Open **Identity**, then **Applications**, then **App registrations**, then
   **All applications**.
3. Find the app with Application (client) ID
   `3bfeff54-14fb-4cee-8b17-2c8d41cad6a8`. Open it, and write down its
   **Supported account types**. Do not change it.
4. Go back, and click **New registration**.
5. **Name:** `Metorite Staging (dev.metorite.com)`.
6. **Supported account types:** choose the SAME value as the production app in
   step 3.
7. **Redirect URI:**
   - **Platform:** `Web`
   - **URI:** exactly this:

     ```
     https://dev.metorite.com/api/auth/callback/microsoft-entra-id
     ```

8. Click **Register**.
9. On the new app's **Overview**, copy the **Application (client) ID**. That is
   `AUTH_MICROSOFT_ENTRA_ID_ID`.
10. Open **Certificates & secrets**, then **Client secrets**, then
    **New client secret**.
    - **Description:** `staging`
    - **Expires:** `24 months`
11. Click **Add**. Copy the **Value** column at once. That is
    `AUTH_MICROSOFT_ENTRA_ID_SECRET`.

⚠️ Copy the **Value**, not the **Secret ID**. The page hides the value when you
leave it.

**Check.** App registrations shows two apps: the production one and
`Metorite Staging (dev.metorite.com)`. The new app's **Authentication** page lists
the redirect URI of step 7.

---

## D. The AI Router key (Operator Console)

Staging calls the same AI Router as production, with its own key. The key belongs
to the organization `hathi-labs-llp`, so staging's AI use draws on Hathi Labs'
own credits and never bills a customer.

1. Open `https://operator.metorite.com` and sign in. You must sign in as an
   operator with the **admin** role. An editor or a viewer gets a refusal. If
   the page asks for elevation, stop and ask the owner.
2. In the customer list, open **Hathi Labs LLP** (slug `hathi-labs-llp`).
3. Find the section **API keys**.
4. **Label (optional):** `staging box (dev.metorite.com)`.
5. Click **Mint a new key**.
6. Copy the key that the page shows. It starts with `cc_live_`. That is
   `CUSTOMER_CONSOLE_ORG_KEY`.

⚠️ The page shows the key ONE time. Do not mint a key on another customer. Do
not revoke a key that is there already: production uses it.

**Check.** The **API keys** section lists the new key with the label of step 4.

> ⚠️ **An owner decision.** With this key, staging's AI spend lands in Hathi
> Labs' credit ledger, and staging has no cap of its own. The other route is a
> new customer `Metorite Staging` from the **New customer** form, which gives a
> separate ledger and cap. That form also grants seats and starts a trial, so
> the customer counts show one more. This runbook uses the Hathi Labs key until
> the owner says otherwise.

---

## E. The setup run on the dev box (Hostinger hPanel, browser terminal)

This part runs one script as root on the dev box. It is the only part that
changes a server.

1. Open `https://hpanel.hostinger.com`.
2. Open **VPS**, then the server `srv1747539` (`187.127.179.143`).

   ⚠️ NOT `srv1914284` (`187.127.172.200`). That is production. The script
   refuses to run there, and you must not try.

3. Open **Browser terminal**. It opens a shell as `root`.
4. Write the secrets file. Type the first line, then paste each value after its
   `=`. Use your five values, with no spaces and no quotes:

   ```bash
   umask 077
   cat > /root/staging.secrets <<'EOF'
   AUTH_GOOGLE_ID=<the value from part B, step 9>
   AUTH_GOOGLE_SECRET=<the value from part B, step 10>
   AUTH_MICROSOFT_ENTRA_ID_ID=<the value from part C, step 9>
   AUTH_MICROSOFT_ENTRA_ID_SECRET=<the value from part C, step 11>
   CUSTOMER_CONSOLE_ORG_KEY=<the value from part D, step 6>
   EOF
   ```

5. Check the file. Each line must show `set`, and no line may show `EMPTY`:

   ```bash
   awk -F= '{print $1, (length($2) > 0 ? "set" : "EMPTY")}' /root/staging.secrets
   ```

6. Get a fresh copy of the code, and run the setup:

   ```bash
   rm -rf /root/metorite-setup
   git clone -q https://github.com/Hathi-Labs/Metorite.git /root/metorite-setup
   bash /root/metorite-setup/deploy/hostinger/bootstrap_staging.sh --secrets /root/staging.secrets
   ```

   The run takes about 5 to 15 minutes. It prints one `==>` line for each step.

7. Read the end of the output. It ends in ONE of two ways.

   - **Way 1, the usual one on the first run.** It prints `ONE STEP LEFT, on the
     production box`, and then one long line that starts with
     `restrict,port-forwarding,`. Copy that whole line. Give it to the agent in
     part F.
   - **Way 2.** It prints `Staging is up at https://dev.metorite.com`. Then part F
     has only its check left.

8. If the run stops with a line that starts with `!!`, copy the last 30 lines of
   the output. Give them to the agent. Do not try a fix. You can run the script
   again after the cause is fixed.

**Errors you may see, and what they mean:**

| The `!!` line says | What to do |
|---|---|
| `this box has the production address` | You are on the production box. Stop. Go back to step 2 |
| `these ports are held by another user` | An agent's dev server is running on the dev box. Give the line to the agent. It stops that server, and you run step 6 again |
| `docker is not installed` | Give the line to the agent |
| `a character that is not allowed` | A value holds a character outside letters, digits and `. _ ~ + / = -`. A space, a quote or a Windows line end are the usual causes. Fix the file (step 4), and run step 6 again |

---

## F. The production side, and the check (the agent on the dev box)

Give the agent on the dev box this message, with the line from part E, step 7:

> Part E of `project-docs/STAGING_SETUP_RUNBOOK.md` is done. Here is the key line:
> `<paste the line>`. Do part F.

**What the agent does in part F.** It does these acts on the production box
`srv1914284`. It reports each act in the same message (CLAUDE.md §3a rule 2):

0. Checks that production serves commit `9ba42141` (PR #1096) or a later one,
   and that `/usr/bin/rrsync` exists there.
   Before that release, production holds no export script. The daily release
   at 03:00 IST brings it, or a manual release run does.
1. Adds the key line to `/home/acb/.ssh/authorized_keys`.
2. Adds this to `/etc/ssh/sshd_config`, runs `sshd -t`, and reloads ssh only when
   the test passes:

   ```
   Match User acb
       AllowStreamLocalForwarding no
   ```

3. Sets `STAGING_EXPORT_SLUGS=default,fracktalworks,hathi-labs-llp,smoke-chat` in
   `/opt/acb/app/.env`. Then it runs `/usr/local/lib/acb/root_env.sh`, which
   copies the name into `/etc/acb/root.env`, where the backup reads it.
4. Runs the nightly backup once, so the first export exists, and confirms
   `/var/lib/acb-staging-export/current/SHA256SUMS`.
5. On the dev box, runs `sudo systemctl start acb-staging-refresh.service`, and
   follows its log to the end.

**The check that closes HANDOFF H-335:**

```bash
curl -s https://api.dev.metorite.com/version
git fetch -q origin staging && git rev-parse origin/staging
```

The `sha` and the `applied_sha` of the first command must both equal the
second. `sha` speaks for the gateway, and `applied_sha` for the whole apply. Then open
`https://dev.metorite.com` in a browser:

- A thin warning stripe is at the top of the page, and the tab reads
  `[Staging] Metorite`.
- **Sign in with Google** and **Sign in with Microsoft** both work, with the same
  account as on production. The session is apart from the production one.
- The organizations are only `default` and `fracktalworks` (both Fracktal
  Works), `hathi-labs-llp` and the internal `smoke-chat`. Prad Automotive, DEWiN
  and Axiom Aerials are not there.

---

## G. After setup

- **Staging takes every merge** to `main` that passes lint, within about 15
  minutes.
- **Production takes one release a day**, at 03:00 IST.
- **The staging data is rebuilt each day** at 09:00 IST, from the production copy
  of the night before.
- **To skip one production release,** set the repository variable `RELEASE_HOLD`
  to `1`. Clear it to go on.
- **For a hotfix,** run `gh workflow run deploy.yml --ref main -f reason="<why>"`.
- **To remove staging later,** stop the `acb-*` services on the dev box and delete
  the two DNS records. Production does not depend on staging.
