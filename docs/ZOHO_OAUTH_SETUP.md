# Register Metorite's Zoho OAuth client

**For:** the owner. **Time:** about 10 minutes. **Slice:** WS-53 CRM-Z0.
**Spec:** `project-docs/specs/crm_platform.md` §5.2 (the Zoho facts) and §12.
**Handoff:** H-297.

Metorite owns ONE Zoho OAuth client. Every customer connects through it, so a
customer never makes an app in Zoho. The customer clicks Connect, gives
consent, and is done. This page tells you how to make that one client and how
to give its two values to an agent.

You do four things:

1. Make the client in the Zoho API Console.
2. Turn on every data centre, with one secret for all of them.
3. Put the client ID and the client secret in your drop folder.
4. Tell an agent that the values are there.

You do not use ssh, gpg or sudo. An agent does the rest.

---

## 1. Before you start

- **Use a Zoho account that Metorite owns and keeps.** The client lives in
  that account. If the person who owns the account leaves, the client goes
  with the account. A shared admin mailbox of Metorite is a good choice.
- **The redirect page does not exist yet.** CRM-Z2 builds the route
  `/api/crm/oauth/zoho/callback`. Until CRM-Z2 merges, a consent ends on a
  404 page. That is correct, and you do not need to test a consent.
- **You do not choose scopes in the console.** Zoho asks for the scopes in
  each consent request. CRM-Z2 sends them (section 5). If a scope changes
  later, the console stays as it is.

## 2. Make the client

1. Open <https://api-console.zoho.com> and sign in with the account from
   section 1.
2. Write down the address that the browser shows after the sign-in. If it is
   not `api-console.zoho.com`, for example `api-console.zoho.in`, tell the
   agent in step 4 of section 4.
3. Click **GET STARTED**, or **ADD CLIENT** if the account already has a
   client.
4. Click **Server-based Applications**. Do not click Self Client. A Self
   Client serves one account only.
5. Fill in the three fields:

   | Field | Value |
   |---|---|
   | Client Name | `Metorite` |
   | Homepage URL | `https://metorite.com` |
   | Authorized Redirect URIs | `https://app.metorite.com/api/crm/oauth/zoho/callback` |

   Type the redirect URI exactly as it shows here. Use `https`. Do not add a
   slash at the end. Zoho refuses a consent when the URI is not the same as
   the one in the console.
6. Click **CREATE**.
7. Open the **Client Secret** tab. It shows the **Client ID** and the
   **Client Secret**. In the samples of Zoho, a client ID starts with `1000.`.
   Do not copy them yet. Do section 3 first.

The customer sees the Client Name on the consent page of Zoho. So the name
must be `Metorite`.

## 3. Turn on every data centre

1. In the client, open the **Settings** tab.
2. Turn on the switch of every data centre in the list. On 2026-10-11 Zoho
   listed eight: United States, Europe, India, Australia, Japan, Canada,
   Saudi Arabia and United Kingdom.
3. Select **Use the same OAuth credentials for all data centers**.
4. Save.

⚠️ **Do step 3.** By default Zoho gives each data centre its own client
secret. Metorite keeps one secret. With a secret for each centre, every
customer outside one centre gets `invalid_client` and cannot connect.

China is not in the list. A customer on Zoho's China data centre cannot
connect through this client. The v1 plan accepts that.

## 4. Give the two values to an agent

The drop folder is `%USERPROFILE%\.metorite\secrets` on your PC. It is
outside every git checkout. `docs/secrets_drop.md` describes it in full.

1. Open `%USERPROFILE%\.metorite\secrets\app.env` in Notepad. If the folder
   or the file is not there, ask an agent to run `scripts/secrets.sh init`
   first. It makes them and writes over nothing.
2. Add these two lines at the end of the file. Use the values from step 7 of
   section 2.

   ```
   CRM_ZOHO_CLIENT_ID=<the Client ID>
   CRM_ZOHO_CLIENT_SECRET=<the Client Secret>
   ```

   Write each line as `KEY=value`. Do not use quotes or spaces. Keep every
   other line of the file as it is.
3. Save the file and close Notepad.
4. Tell an agent: "The Zoho client is in the drop." If the console address in
   step 2 of section 2 was not `api-console.zoho.com`, give that address too.

⚠️ **Do not paste the secret in a chat, a ticket, an email or a commit.** The
drop folder is the only place for it.

## 5. What the agent does next

You do not do these steps. They are here so that you know what happens.

1. The agent adds `CRM_ZOHO_CLIENT_ID` and `CRM_ZOHO_CLIENT_SECRET` to
   `allowed_keys` of the `app-env` entry in `deploy/secrets/manifest.json`, in
   a reviewed change. Until then `push` refuses the two keys. The manifest
   lets only the keys that it names into the app env file.
2. The agent runs `scripts/secrets.sh diff app-env`. It shows the two key
   names with a `+`, and never a value.
3. The agent runs `scripts/secrets.sh push app-env`. The script merges the
   two keys into `/opt/acb/app/.env` on the box, and it keeps every other
   line. It keeps a backup of the old file, restarts `acb-gateway` and checks
   its health. If the gateway does not stay healthy, it puts the old file
   back.
4. The agent runs `scripts/secrets.sh status app-env`. The result
   `in-sync` proves that the box holds your values.
5. CRM-Z2 reads the two keys and asks for these read-only scopes in each
   consent:

   | Scope | Why |
   |---|---|
   | `ZohoCRM.modules.leads.READ` | Leads, and their deleted list |
   | `ZohoCRM.modules.contacts.READ` | Contacts, and their deleted list |
   | `ZohoCRM.modules.accounts.READ` | Accounts (companies), and their deleted list |
   | `ZohoCRM.modules.deals.READ` | Deals, and their deleted list |
   | `ZohoCRM.modules.notes.READ` | Notes |
   | `ZohoCRM.modules.calls.READ` | Calls |
   | `ZohoCRM.modules.events.READ` | Meetings, which the API calls Events |
   | `ZohoCRM.settings.fields.READ` | The field definitions of each module |
   | `ZohoCRM.settings.layouts.READ` | The Deals layouts, which hold the pipelines |
   | `ZohoCRM.settings.pipeline.READ` | The pipelines and their stages |
   | `ZohoCRM.settings.modules.READ` | The module names for the link to a record |
   | `ZohoCRM.users.READ` | The users, to match an owner to a member by email |
   | `ZohoCRM.org.READ` | The org name for the link to a record |

   Two scopes come only when a slice needs them: `ZohoCRM.bulk.READ` for a
   Bulk Read import (CRM-Z4), and `ZohoCRM.modules.tasks.READ` to mirror Zoho
   Tasks. A new scope needs a new consent from each connected customer.

## 6. Review by Zoho

On 2026-10-11 no Zoho page named a review or a verification of an app before
users of other organizations can consent. So none is known. Each customer
admin sees the consent page of Zoho with the scopes, and accepts or refuses.

Zoho can show a message about an app that it has not reviewed. Zoho can also
refuse a consent from another organization. In both cases, tell an agent the
exact words. The agent adds them to §5.2 of the spec.

## 7. How to check that you are done

An agent runs these checks. Each one prints a count and never a value.

```bash
grep -cE '^CRM_ZOHO_CLIENT_(ID|SECRET)=.+' "$HOME/.metorite/secrets/app.env"
scripts/secrets.sh status app-env
```

The first command must print `2`. The second must show `in-sync`.

## 8. If you must start again

- **The secret leaked.** In the client, open the Client Secret tab and make a
  new secret. Put the new value in `app.env`, and tell an agent. Customers
  stay connected, because their refresh tokens do not depend on the secret.
  ⚠️ This last point is not confirmed by a Zoho page. CRM-Z2 tests it.
- **You deleted the client.** Every customer connection stops. Make a new
  client with this page. Each customer then connects again.
