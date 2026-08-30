// The Customer Console operator-API client — SERVER-SIDE ONLY.
//
// ⚠️ This module holds `CUSTOMER_CONSOLE_OPERATOR_TOKEN`. That token is a
// cross-organization staff credential (customer_console.md §6, the `Operator`
// scheme) and it MUST NEVER reach the browser — the same rule as the deployment
// key. Every call to the Console goes through `callConsole`, which puts the
// token in the OUTGOING request's `Authorization` header and nowhere else; the
// BFF routes return only the Console's own JSON body to the browser. No client
// component imports this file (fenced by `console.test.ts`'s source scan), so
// the token cannot be bundled into client output.
//
// CP-8 done-when: the console "renders from the Customer Console alone with no
// per-deployment round trip on the request path" — this client talks to
// `CUSTOMER_CONSOLE_URL` (the operator API) only, never to a tenant deployment.

export type ConsoleEnv = {
  url?: string;
  operatorToken?: string;
};

export class ConsoleUnconfigured extends Error {}

export type FetchResponse = { status: number; text: () => Promise<string> };
export type FetchLike = (
  url: string,
  init: { method: string; headers: Record<string, string>; body?: string },
) => Promise<FetchResponse>;

export type ConsoleResult = { status: number; body: string };

const envTrim = (v: string | undefined): string | undefined => {
  const t = (v ?? "").trim();
  return t.length > 0 ? t : undefined;
};

export function readConsoleEnv(
  env: Record<string, string | undefined> = process.env,
): ConsoleEnv {
  return {
    url: envTrim(env.CUSTOMER_CONSOLE_URL),
    operatorToken: envTrim(env.CUSTOMER_CONSOLE_OPERATOR_TOKEN),
  };
}

// The token lives ONLY here, on the request to the Console. Fails CLOSED when
// unconfigured — a dark deployment with no token refuses rather than issuing an
// unauthenticated cross-org call.
export function operatorHeaders(env: ConsoleEnv): Record<string, string> {
  if (!env.url || !env.operatorToken) {
    throw new ConsoleUnconfigured(
      "CUSTOMER_CONSOLE_URL and CUSTOMER_CONSOLE_OPERATOR_TOKEN must both be " +
        "set server-side before the operator console can reach the Console",
    );
  }
  return {
    Authorization: `Bearer ${env.operatorToken}`,
    "Content-Type": "application/json",
  };
}

// The operator's OWN session on the wire (CP-12g). The Console URL still has
// to be configured — an unconfigured box refuses rather than guessing a host —
// but the shared operator token is deliberately NOT required here. A console
// that demanded it would keep a cross-org master credential on disk for no
// reason once real sessions exist.
export function sessionHeaders(
  env: ConsoleEnv,
  token: string,
): Record<string, string> {
  if (!env.url) {
    throw new ConsoleUnconfigured(
      "CUSTOMER_CONSOLE_URL must be set server-side before the operator " +
        "console can reach the Console",
    );
  }
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

// ⚠️ **CP-12g: whose credential goes on the wire.**
//
// `authToken` is the SIGNED-IN OPERATOR's `cc_sess_` session. When it is
// present it replaces the shared operator token entirely, and that is the
// whole point of CP-12: the audit row then names a person instead of reading
// `breakglass`, and the §5 role matrix applies to them.
//
// Falling back to the env token for a user-driven call would be worse than
// doing nothing. CP-12e made the shared token BYPASS every role and elevation
// check, and log a WARNING on each use. A console that proxied through it
// would turn ordinary work into a stream of break-glass alerts, and hand every
// signed-in person admin rights. `route.ts` therefore REFUSES rather than
// falls back whenever the session path is on.
export async function callConsole(
  path: string,
  init: { method: string; body?: unknown },
  deps: { env?: ConsoleEnv; fetchImpl?: FetchLike; authToken?: string } = {},
): Promise<ConsoleResult> {
  const env = deps.env ?? readConsoleEnv();
  const headers = deps.authToken
    ? sessionHeaders(env, deps.authToken)
    : operatorHeaders(env);
  const fetchImpl = deps.fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const res = await fetchImpl(`${env.url}${path}`, {
    method: init.method,
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  return { status: res.status, body: await res.text() };
}

// ── The operator API surface the console consumes ───────────────────────────
//
// Reuse the Console routes that already exist and are PROVEN LIVE — never a
// reimplementation (customer_console.md §6).
//
// ⚠️ Every helper takes `authToken`. One that could not carry the caller's
// session would silently fall back to the shared token, which is the exact
// mistake `callConsole`'s comment above describes.

export type Deps = {
  env?: ConsoleEnv;
  fetchImpl?: FetchLike;
  authToken?: string;
};

export const listOrganizations = (d?: Deps) =>
  callConsole("/orgs", { method: "GET" }, d ?? {});

export const billingSummary = (orgSlug: string, d?: Deps) =>
  callConsole(
    `/billing/summary?org_slug=${encodeURIComponent(orgSlug)}`,
    { method: "GET" },
    d ?? {},
  );

// ── Operator usage (WS-31, `specs/ai_metering_and_analytics.md` §5) ────────
// ⚠️ Both cross tenants. The Console gates them on the operator role, so the
// caller's OWN token must be passed — a shared token reaches the Console as
// `breakglass` and bypasses the role matrix.

export const orgUsage = (days: number, d?: Deps) =>
  callConsole(
    `/admin/usage/orgs?days=${encodeURIComponent(String(days))}`,
    { method: "GET" },
    d ?? {},
  );

export const usageDaily = (days: number, orgSlug?: string, d?: Deps) =>
  callConsole(
    `/admin/usage/daily?days=${encodeURIComponent(String(days))}` +
      (orgSlug ? `&org_slug=${encodeURIComponent(orgSlug)}` : ""),
    { method: "GET" },
    d ?? {},
  );

export const catalog = (d?: Deps) =>
  callConsole("/billing/catalog", { method: "GET" }, d ?? {});

export const activateSubscription = (body: unknown, d?: Deps) =>
  callConsole(
    "/billing/subscriptions/activate",
    { method: "POST", body },
    d ?? {},
  );

export const assignSeat = (body: unknown, d?: Deps) =>
  callConsole("/billing/seats", { method: "POST", body }, d ?? {});

export const releaseSeat = (body: unknown, d?: Deps) =>
  callConsole("/billing/seats/release", { method: "POST", body }, d ?? {});

export const grantCredits = (body: unknown, d?: Deps) =>
  callConsole("/credits/grant", { method: "POST", body }, d ?? {});

export const setLifecycle = (body: unknown, d?: Deps) =>
  callConsole("/orgs/lifecycle", { method: "POST", body }, d ?? {});

// CP-2g — the registry half of destroying an organization. The Console refuses
// unless the org is already `deleted` (terminal on the lifecycle graph), strips
// personal data + live secrets, tombstone-renames the slug, and keeps the
// financial record. Body {org_slug, confirm} where confirm must echo the slug.
export const purgeOrgRegistry = (body: unknown, d?: Deps) =>
  callConsole("/orgs/purge", { method: "POST", body }, d ?? {});

// Provision a new organization (create-only): the Console POST /orgs/provision
// under the `Operator` scheme creates the org + its owner + Core seats, PLACES
// it on the named deployment and starts a trial subscription in one idempotent
// call. `deployment_label` is REQUIRED under the operator scheme (a cross-org
// staff credential carries no deployment identity); the Console answers 400 when
// it is missing, 404 for an unknown label and 409 when the org is already placed
// on a different deployment — this client relays each status verbatim.
export const provisionOrg = (body: unknown, d?: Deps) =>
  callConsole("/orgs/provision", { method: "POST", body }, d ?? {});

// ── CP-11 slice 1 — the ORGANIZATION key (`cc_live_`) ───────────────────────
//
// ⚠️ **Not the provider credential.** `customer_console.md` §6B.2 tabulates the
// pair because they are the most confusable in the system:
//
//   provider_credential  OURS   — the DeepSeek/Anthropic secret, Fernet at
//                                 rest, presented BY the Router TO the vendor.
//   llm_api_key          THEIRS — one customer's `cc_live_` key, stored as a
//                                 HASH, presented BY their box TO the Router.
//
// A leak of the first is our whole vendor bill. A leak of the second is one
// organization's AI spend. They are not interchangeable and must not be merged.
//
// ⚠️ **`issueKey` returns the token EXACTLY ONCE.** The Console stores only the
// hash, so the response is the only moment the secret exists anywhere. It is not
// recoverable — a lost key is replaced, never looked up. Any caller that
// discards this response has destroyed the key.
// The ledger history behind the balance — what an operator verifies a bank
// transfer against before granting (the dedupe fence's read half).
export const creditLedger = (orgSlug: string, d?: Deps) =>
  callConsole(
    `/credits/ledger?org_slug=${encodeURIComponent(orgSlug)}`,
    { method: "GET" },
    d ?? {},
  );

export const listKeys = (orgSlug: string, d?: Deps) =>
  callConsole(
    `/keys?org_slug=${encodeURIComponent(orgSlug)}`,
    { method: "GET" },
    d ?? {},
  );

export const issueKey = (body: unknown, d?: Deps) =>
  callConsole("/keys", { method: "POST", body }, d ?? {});

export const revokeKey = (body: unknown, d?: Deps) =>
  callConsole("/keys/revoke", { method: "POST", body }, d ?? {});

// ── CP-12: operator identity ────────────────────────────────────────────────

// The sign-in exchange (CP-12f2). ⚠️ This one carries NO credential of ours.
// The Supabase access token in the body is the whole of the proof, and the
// Console verifies it with the issuer before it mints anything.
export const exchangeSession = (accessToken: string, d?: Deps) =>
  callConsole(
    "/operators/session",
    { method: "POST", body: { access_token: accessToken } },
    d ?? {},
  );

export const revokeSession = (d?: Deps) =>
  callConsole("/operators/session", { method: "DELETE" }, d ?? {});

export const listOperators = (d?: Deps) =>
  callConsole("/operators", { method: "GET" }, d ?? {});

export const addOperator = (body: unknown, d?: Deps) =>
  callConsole("/operators", { method: "POST", body }, d ?? {});

export const updateOperator = (id: string, body: unknown, d?: Deps) =>
  callConsole(
    `/operators/${encodeURIComponent(id)}`,
    { method: "PATCH", body },
    d ?? {},
  );

export const deactivateOperator = (id: string, d?: Deps) =>
  callConsole(
    `/operators/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    d ?? {},
  );

// The elevation window (CP-12e). Opening one is always for the CALLER, so
// there is no operator id to pass — the Console reads it from the session.
export const readElevation = (d?: Deps) =>
  callConsole("/operators/elevate", { method: "GET" }, d ?? {});

export const openElevation = (body: unknown, d?: Deps) =>
  callConsole("/operators/elevate", { method: "POST", body }, d ?? {});

export const closeElevation = (d?: Deps) =>
  callConsole("/operators/elevate", { method: "DELETE" }, d ?? {});

// The MODEL catalog (CP-10 slice 3) — capabilities, tier bindings and rates.
//
// ⚠️ **Not the same catalog as `catalog()` above**, which is the priced PLAN
// ladder (`/billing/catalog`). The Console has carried both names since CP-6
// and this file inherits the ambiguity rather than inventing a third word for
// one of them. These four map to the Console's own `/catalog/*` paths.
//
// ⚠️ The two WRITES that matter are `admin` AND need a live elevation
// window (§5 matrix). The Console enforces that; nothing here does.
export const readModelCatalog = (d?: Deps) =>
  callConsole("/catalog/models", { method: "GET" }, d ?? {});

export const declareCapability = (body: unknown, d?: Deps) =>
  callConsole("/catalog/capabilities", { method: "POST", body }, d ?? {});

export const bindTier = (body: unknown, d?: Deps) =>
  callConsole("/catalog/bindings", { method: "POST", body }, d ?? {});

export const setModelRate = (body: unknown, d?: Deps) =>
  callConsole("/catalog/rates", { method: "POST", body }, d ?? {});

// D67: what a CUSTOMER pays is keyed on the tier they picked. Admin plus an
// elevation window, same as the model-keyed card it replaces.
export const setTierRate = (body: unknown, d?: Deps) =>
  callConsole("/catalog/tier-rates", { method: "POST", body }, d ?? {});

// The credit's own rupee price (017) — the other half of H-42. Admin plus
// an elevation window; billing never reads what it writes.
export const setCreditPrice = (body: unknown, d?: Deps) =>
  callConsole("/catalog/credit-price", { method: "POST", body }, d ?? {});

// What a model IS (migration 012). ⚠️ The only catalog write that is an UPSERT
// rather than an insert, and the only one gated at `editor` without an
// elevation window — a context window is a fact about the world, not a
// commercial decision that owes an audit trail.
export const setModelProfile = (body: unknown, d?: Deps) =>
  callConsole("/catalog/profiles", { method: "POST", body }, d ?? {});

// Pull the vendor feed (migration 014) — litellm's price map — into
// `vendor_price_feed` NOW. Reference data only: nothing billing reads moves.
export const syncVendorFeed = (d?: Deps) =>
  callConsole("/catalog/feed/sync", { method: "POST", body: {} }, d ?? {});

// The audit trail (CP-12f). Filters and the opaque page cursor ride as query
// parameters, and every one of them is optional.
export const readActivity = (query: string, d?: Deps) =>
  callConsole(
    `/activity${query ? `?${query}` : ""}`,
    { method: "GET" },
    d ?? {},
  );

export const activityActions = (d?: Deps) =>
  callConsole("/activity/actions", { method: "GET" }, d ?? {});

// ── OUR provider accounts (CP-10 slice 4) ───────────────────────────────────
//
// ⚠️ **This is the `provider_credential` half of the pair tabulated above**,
// not `llm_api_key`. A leak here is our whole vendor bill.
//
// ⚠️ **The secret is WRITE-ONLY and the read proves it structurally.** The
// Console's list query does not select `secret_enc`, so `listProviderCreds`
// cannot return a key or a fragment of one — there is nothing to redact
// because there is nothing there.
//
// ⚠️ **Installing over a live credential ROTATES it**, in one transaction:
// the old row is revoked, the new one inserted, and there is never a moment
// with two live keys or none. That is the same POST, not a second route.
export const listProviderCreds = (includeRevoked: boolean, d?: Deps) =>
  callConsole(
    `/providers/credentials${includeRevoked ? "?include_revoked=true" : ""}`,
    { method: "GET" },
    d ?? {},
  );

// 🔴 `admin` AND a live elevation window. The Console enforces it; this does
// not. Installing the PLATFORM credential arms every AI call we serve.
export const installProviderCred = (body: unknown, d?: Deps) =>
  callConsole("/providers/credentials", { method: "POST", body }, d ?? {});

// 🔴 Revoking the PLATFORM credential stops every AI call that is not BYOK.
export const revokeProviderCred = (body: unknown, d?: Deps) =>
  callConsole("/providers/credentials/revoke", { method: "POST", body }, d ?? {});
