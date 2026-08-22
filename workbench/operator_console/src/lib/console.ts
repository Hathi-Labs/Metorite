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

export async function callConsole(
  path: string,
  init: { method: string; body?: unknown },
  deps: { env?: ConsoleEnv; fetchImpl?: FetchLike } = {},
): Promise<ConsoleResult> {
  const env = deps.env ?? readConsoleEnv();
  const headers = operatorHeaders(env);
  const fetchImpl = deps.fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const res = await fetchImpl(`${env.url}${path}`, {
    method: init.method,
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  return { status: res.status, body: await res.text() };
}

// ── The operator API surface CP-8 slice 1 consumes ──────────────────────────
//
// Reuse the Console routes that already exist and are PROVEN LIVE — never a
// reimplementation (customer_console.md §6). Only the cross-org list (`GET
// /orgs`, added by this ticket) is new, and it too lives on the Console.

export const listOrganizations = (d?: {
  env?: ConsoleEnv;
  fetchImpl?: FetchLike;
}) => callConsole("/orgs", { method: "GET" }, d ?? {});

export const billingSummary = (
  orgSlug: string,
  d?: { env?: ConsoleEnv; fetchImpl?: FetchLike },
) =>
  callConsole(
    `/billing/summary?org_slug=${encodeURIComponent(orgSlug)}`,
    { method: "GET" },
    d ?? {},
  );

export const catalog = (d?: { env?: ConsoleEnv; fetchImpl?: FetchLike }) =>
  callConsole("/billing/catalog", { method: "GET" }, d ?? {});

export const activateSubscription = (
  body: unknown,
  d?: { env?: ConsoleEnv; fetchImpl?: FetchLike },
) => callConsole("/billing/subscriptions/activate", { method: "POST", body }, d ?? {});

export const assignSeat = (
  body: unknown,
  d?: { env?: ConsoleEnv; fetchImpl?: FetchLike },
) => callConsole("/billing/seats", { method: "POST", body }, d ?? {});

export const releaseSeat = (
  body: unknown,
  d?: { env?: ConsoleEnv; fetchImpl?: FetchLike },
) => callConsole("/billing/seats/release", { method: "POST", body }, d ?? {});

export const grantCredits = (
  body: unknown,
  d?: { env?: ConsoleEnv; fetchImpl?: FetchLike },
) => callConsole("/credits/grant", { method: "POST", body }, d ?? {});

export const setLifecycle = (
  body: unknown,
  d?: { env?: ConsoleEnv; fetchImpl?: FetchLike },
) => callConsole("/orgs/lifecycle", { method: "POST", body }, d ?? {});

// Provision a new organization (create-only): the Console POST /orgs/provision
// under the `Operator` scheme creates the org + its owner + Core seats, PLACES
// it on the named deployment and starts a trial subscription in one idempotent
// call. `deployment_label` is REQUIRED under the operator scheme (a cross-org
// staff credential carries no deployment identity); the Console answers 400 when
// it is missing, 404 for an unknown label and 409 when the org is already placed
// on a different deployment — this client relays each status verbatim.
export const provisionOrg = (
  body: unknown,
  d?: { env?: ConsoleEnv; fetchImpl?: FetchLike },
) => callConsole("/orgs/provision", { method: "POST", body }, d ?? {});
