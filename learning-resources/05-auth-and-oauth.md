# 05 · Authentication & OAuth

There are **three distinct auth problems** in an agent platform, and beginners routinely conflate them:

1. **Who is the human?** — user login to the web app (SSO).
2. **Is this API call allowed?** — protecting the backend gateway (machine trust + user identity + roles).
3. **How does an agent act on a third party's behalf?** — OAuth *to* ClickUp/Zoho/Gmail/Microsoft, with
   token storage and refresh.

They're solved by different mechanisms. This chapter takes them one at a time, then shows how they
compose into a single request.

---

## 1. Problem 1 — Who is the human? (Frontend SSO)

The Next.js Control Plane uses **NextAuth v5** with **Microsoft Entra ID** (Azure AD) as the sole
identity provider. The whole config is a provider block plus two callbacks that copy the email/name into
the session:

```typescript
export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [MicrosoftEntraId({
    clientId: process.env.AUTH_MICROSOFT_ENTRA_ID_ID ?? "",
    clientSecret: process.env.AUTH_MICROSOFT_ENTRA_ID_SECRET ?? "",
    issuer: `https://login.microsoftonline.com/${TENANT}/v2.0`,
  })],
  callbacks: { /* copy profile.email → token → session.user.email */ },
  pages: { signIn: "/signin" },
});
export const isAuthEnabled = Boolean(process.env.AUTH_MICROSOFT_ENTRA_ID_ID);
```

Two things worth copying:
- **Tenant-scoping.** The `issuer` is pinned to a specific Azure AD tenant, so only accounts in *your*
  organization can sign in. That's your coarse "employees only" gate for free.
- **Graceful dev bypass.** If `AUTH_MICROSOFT_ENTRA_ID_ID` isn't set, auth is disabled and everything is
  allowed. Local development doesn't need a real IdP. (Make sure it *is* set in prod.)

After login, the browser holds a **signed session cookie**. It never holds any backend or third-party
secret. That matters for the next problem.

---

## 2. Problem 2 — Is this API call allowed? (Gateway auth)

Recall from chapter 02 that the browser talks to the gateway only through a **Next.js server-side
proxy**. That proxy is where the two credentials the gateway needs get attached:

```typescript
// Runs server-side in the Next.js API route — the browser never sees INTERNAL_TOKEN
const session = await auth();
const headers = {
  Authorization: `Bearer ${INTERNAL_TOKEN}`,                       // (a) machine trust
  "X-User-Email": session.user.email,                              // (b) who
  "X-User-Role": EXEC_EMAILS.has(email) ? "executive" : "employee" // (c) what they may do
};
```

On the gateway side, one FastAPI dependency, `get_current_user`, turns those headers into a
`UserContext`. Its logic (in `packages/acb_auth/deps.py`) is deliberately **layered and never
throws** — it resolves the *lowest* privilege it can justify rather than rejecting:

1. **Bearer token check** (constant-time `hmac.compare_digest` against `GATEWAY_INTERNAL_TOKEN`, which
   falls back to `LITELLM_MASTER_KEY` in dev). A match = the caller is a trusted machine.
   - With an `X-User-Email` header → a trusted request carrying a real user identity.
   - Without one → a synthetic service identity (`system:internal`, role `AGENT`).
2. **SSO headers without a Bearer token** → direct browser/dev access; build the context from the
   headers.
3. **Domain enforcement:** the email must end in the allowed domain (`fracktal.in`); otherwise it's
   treated as anonymous.

Roles then gate specific endpoints declaratively:

```python
@app.post("/pull/sales", dependencies=[require_role(UserRole.EXECUTIVE)])
async def pull_sales(req: PullRequest): ...
```

`require_role` raises `403` if the caller's role isn't allowed. Three roles exist: `EXECUTIVE`,
`EMPLOYEE`, `AGENT` (service-to-service).

**The pattern to internalize — two-factor gateway trust:** *(a)* a shared machine secret proves the
call came from your own frontend/proxy (not a random internet client), and *(b)* forwarded identity
headers say which human is behind it. The machine secret lives only in server processes; the human
identity rides along for scoping and audit. Never put the machine secret in the browser.

---

## 3. Problem 3 — Acting on a third party's behalf (OAuth to external APIs)

This is the hard one, and the part most tutorials skip. To read a user's Gmail or write to their Zoho,
your platform needs an **OAuth 2.0 authorization-code** grant: the user consents once, you receive tokens,
and you use (and refresh) those tokens for months afterward.

Metorite has a **generic OAuth framework** (`gateway/routes/oauth.py`) driven by a provider
registry. Adding a provider is mostly filling in a struct (authorize URL, token URL, scopes, which env
vars hold the resulting tokens). Zoho, ClickUp, and Google are wired in.

### The flow, step by step

```
 ┌ user in the app clicks "Connect Zoho"
 │
 ▼
GET /integrations/oauth/zoho/authorize        (auth required — we know who's asking)
 │   • look up client_id
 │   • mint an HMAC-SIGNED state token:  {service}:{nonce}:{ts}:{sig}    ← CSRF defense
 │   • return the provider consent URL (carrying state)
 ▼
browser → provider consent screen → user approves
 │
 ▼
GET /integrations/oauth/callback/zoho?code=…&state=…    (PUBLIC — the provider calls it)
 │   • verify state HMAC + TTL (≤10 min)                ← rejects forged/stale callbacks
 │   • POST code → provider token URL, get access + refresh tokens
 │   • persist tokens (encrypted)                       ← see §4
 ▼
tokens now usable by agents; refreshed automatically before expiry
```

Two security details that are easy to get wrong and important to get right:

- **The `state` parameter is an HMAC-signed, time-limited token, not a random opaque string you merely
  echo back.** Signing it (keyed by a server secret) means an attacker can't forge a callback, and the
  TTL means a leaked callback URL goes stale fast. This is your CSRF protection for the OAuth dance.
- **The callback endpoint is necessarily public** (the provider's servers call it, unauthenticated), so
  *all* of its trust comes from validating that `state`. Treat it as hostile input otherwise.

### Token refresh — the operational reality

Access tokens expire fast (Microsoft/Google: ~1 hour). Before using a credential, the platform refreshes
it if it's within ~5 minutes of expiry, using the stored refresh token. Two provider quirks the code
handles that *will* bite you if you don't:

- **Microsoft rotates the refresh token on every refresh.** You must persist the *new* refresh token from
  each refresh response, or the next refresh fails with a stale token. (Google *may* return a new one;
  Zoho does *not* rotate.)
- **ClickUp returns no refresh token at all** — its access token is long-lived, so there's simply nothing
  to refresh.

The general rule: **every provider's OAuth is subtly different; encode each one's refresh contract
explicitly** rather than assuming a uniform flow.

### Email OAuth specifically

The email assistant has its own OAuth path (`email/transport/oauth.py`) for Gmail and Microsoft 365,
because it needs richer scopes and per-mailbox handling:

- **Microsoft scopes:** `offline_access` (to get a refresh token at all), `Mail.ReadWrite`, `Mail.Send`,
  `User.Read`, `MailboxSettings.ReadWrite`.
- After the token exchange, it calls the provider's "who am I" endpoint (`graph.microsoft.com/v1.0/me`
  or the Gmail profile endpoint) to learn the connected address, then stores an **encrypted** credential
  blob against that `email_accounts` row and kicks off a background sync.
- The stored blob includes the OAuth *app* client_id/secret alongside the user tokens — because refresh
  needs them, and you don't want to rely on ambient env vars at refresh time.

---

## 4. Where third-party tokens live: the encrypted key store

All external credentials — LLM provider keys, integration API keys, and OAuth tokens — are stored
**encrypted at rest in Postgres**, never in plaintext files or agent repos. The mechanism
(`packages/acb_llm/key_store.py`):

- A single `ACB_MASTER_KEY` (env var) is stretched via **PBKDF2-HMAC-SHA256** (480k iterations) into a
  32-byte **Fernet** (AES) key.
- Secrets are encrypted with that key and written to a `provider_keys` table (and email creds to
  `email_accounts.credentials_encrypted`). Decryption happens in-memory at use time; secrets are never
  logged.
- On first boot the store **seeds itself from `.env`** (a one-time migration), after which Postgres is the
  source of truth and the UI can rotate keys without a redeploy.

This ties back to the platform's **Integration Registry** principle (chapter 06/10): *agents declare
which integrations they need by name; the platform owns the actual credentials and injects only the
declared ones at runtime.* No secret ever lives in an agent's Git repo.

---

## 5. How it all composes in one request

A user asks the chat to "draft a reply to the Acme thread":

```
Browser (session cookie)
  → Next.js proxy: reads session, attaches Bearer INTERNAL_TOKEN + X-User-Email + X-User-Role
    → Gateway: get_current_user validates Bearer (machine) + builds UserContext (human) + role gate
      → Orchestrator loads the email agent, and the loader injects the user's Gmail/MS OAuth token
         (decrypted from the key store, refreshed if near expiry)
        → Agent calls the mail API with that token, drafts the reply
```

Four different auth mechanisms, one clean line of trust: **SSO** established *who*, the **Bearer token**
proved the call came from your own frontend, **roles** decided it was permitted, and **OAuth tokens**
(decrypted, refreshed, injected) let the agent actually touch Gmail — without any secret ever reaching
the browser or an agent repo.

Next: **[06 · The Orchestration System](./06-orchestration.md)**.
