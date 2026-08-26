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
