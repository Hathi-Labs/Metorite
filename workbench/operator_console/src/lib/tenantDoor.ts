// The gateway operator-door client — SERVER-SIDE ONLY. (CP-2g)
//
// ⚠️ This module holds `GATEWAY_OPERATOR_TOKEN`, the credential for the
// gateway's `/internal/operator/*` door — the `console.ts` rule applies
// verbatim: the token goes on the OUTGOING request's `Authorization` header
// and nowhere else, and no client component may import this file (the same
// source scan in `console.test.ts` fences it).
//
// This is the operator console's ONLY road to the tenant plane. It serves
// three acts, and no others:
//
//   1. `purgeTenantOrg` destroys an organization's tenant data after the
//      Console (the lifecycle authority) already holds it at `deleted`.
//   2. `removeTenantMember` removes one member, or deletes them permanently.
//      The owner asked for it on 2026-10-15, so an operator can take a person
//      out of a customer's organization (invited or signed in).
//   3. `renameTenantOrg` sets the organization's display name. The owner
//      asked for it on 2026-10-27 (`customer_console.md` CP-8 "Rename an
//      organization"), so one rename changes the name on both planes.
//
// Everything else the console does stays Console-only (CP-8's "no
// per-deployment round trip" rule). A fourth gateway call added here is a
// defect until a spec says otherwise.

import type { ConsoleResult, FetchLike } from "./console";
import type { MemberRemoveMode } from "./memberRemove";

export type TenantDoorEnv = {
  url?: string;
  /** The gateway's ordinary machine bearer — clears the APP-LEVEL auth gate. */
  internalToken?: string;
  /** The door's own credential, sent as X-Operator-Token. */
  operatorToken?: string;
};

export class TenantDoorUnconfigured extends Error {}

/** Are all three values set? The member route asks this BEFORE any upstream
 *  call, so an unconfigured door refuses with nothing touched. */
export function tenantDoorConfigured(
  env: TenantDoorEnv = readTenantDoorEnv(),
): boolean {
  return Boolean(env.url && env.internalToken && env.operatorToken);
}

const envTrim = (v: string | undefined): string | undefined => {
  const t = (v ?? "").trim();
  return t.length > 0 ? t : undefined;
};

export function readTenantDoorEnv(
  env: Record<string, string | undefined> = process.env,
): TenantDoorEnv {
  return {
    url: envTrim(env.GATEWAY_INTERNAL_URL),
    internalToken: envTrim(env.GATEWAY_INTERNAL_TOKEN),
    operatorToken: envTrim(env.GATEWAY_OPERATOR_TOKEN),
  };
}

// DELETE the organization's tenant plane. TWO tokens, deliberately: the
// gateway's app-level `require_authenticated` consumes `Authorization` (the
// door is NOT in its public-routes exemption list), and the door itself then
// demands `X-Operator-Token`. Neither credential alone reaches the purge —
// the internal token has an unprovisioned-box fallback agents hold, and the
// operator token must not bypass the gateway's ordinary gate.
//
// Fails CLOSED when unconfigured — a dark deployment refuses rather than
// skipping the tenant half silently (a "purge" that only stripped the
// registry would report success while the customer's data survived).
export async function purgeTenantOrg(
  slug: string,
  deps: { env?: TenantDoorEnv; fetchImpl?: FetchLike } = {},
): Promise<ConsoleResult> {
  const env = deps.env ?? readTenantDoorEnv();
  if (!env.url || !env.internalToken || !env.operatorToken) {
    throw new TenantDoorUnconfigured(
      "the gateway URL, internal token and operator-door token must all be " +
        "set server-side before the operator console can purge tenant data",
    );
  }
  const fetchImpl = deps.fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const res = await fetchImpl(
    `${env.url}/internal/operator/organizations/${encodeURIComponent(slug)}` +
      `?confirm=${encodeURIComponent(slug)}`,
    {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${env.internalToken}`,
        "X-Operator-Token": env.operatorToken,
      },
    },
  );
  return { status: res.status, body: await res.text() };
}

// DELETE one member on the tenant plane. The same two tokens as the org purge
// above, for the same reason. The address goes in the path, and again as
// `confirm`, verbatim: the gateway door re-checks the pair itself.
//
// Fails CLOSED when unconfigured, before any request. A Console-only removal
// would report success while the person kept their access to the tenant.
export async function removeTenantMember(
  slug: string,
  email: string,
  mode: MemberRemoveMode,
  deps: { env?: TenantDoorEnv; fetchImpl?: FetchLike } = {},
): Promise<ConsoleResult> {
  const env = deps.env ?? readTenantDoorEnv();
  if (!env.url || !env.internalToken || !env.operatorToken) {
    throw new TenantDoorUnconfigured(
      "the gateway URL, internal token and operator-door token must all be " +
        "set server-side before the operator console can remove a member",
    );
  }
  const fetchImpl = deps.fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const res = await fetchImpl(
    `${env.url}/internal/operator/organizations/${encodeURIComponent(slug)}` +
      `/members/${encodeURIComponent(email)}` +
      `?mode=${encodeURIComponent(mode)}&confirm=${encodeURIComponent(email)}`,
    {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${env.internalToken}`,
        "X-Operator-Token": env.operatorToken,
      },
    },
  );
  return { status: res.status, body: await res.text() };
}

// PATCH the organization's display name on the tenant plane (CP-8). The same
// two tokens as the acts above. The slug goes in the PATH, because the
// gateway takes the tenant from the path. The body carries the name and
// nothing else. The gateway cleans and checks the name, and answers 422 for
// one it refuses.
//
// Fails CLOSED when unconfigured, before any request. A Console-only rename
// would report success while the customer's app kept the old name.
export async function renameTenantOrg(
  slug: string,
  name: string,
  deps: { env?: TenantDoorEnv; fetchImpl?: FetchLike } = {},
): Promise<ConsoleResult> {
  const env = deps.env ?? readTenantDoorEnv();
  if (!env.url || !env.internalToken || !env.operatorToken) {
    throw new TenantDoorUnconfigured(
      "the gateway URL, internal token and operator-door token must all be " +
        "set server-side before the operator console can rename an organization",
    );
  }
  const fetchImpl = deps.fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const res = await fetchImpl(
    `${env.url}/internal/operator/organizations/${encodeURIComponent(slug)}`,
    {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${env.internalToken}`,
        "X-Operator-Token": env.operatorToken,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ name }),
    },
  );
  return { status: res.status, body: await res.text() };
}
