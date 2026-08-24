// The gateway operator-door client — SERVER-SIDE ONLY. (CP-2g)
//
// ⚠️ This module holds `GATEWAY_OPERATOR_TOKEN`, the credential for the
// gateway's `/internal/operator/*` door — the `console.ts` rule applies
// verbatim: the token goes on the OUTGOING request's `Authorization` header
// and nowhere else, and no client component may import this file (the same
// source scan in `console.test.ts` fences it).
//
// This is the operator console's ONLY road to the tenant plane, and it exists
// for exactly one act: destroying an organization's tenant data after the
// Console (the lifecycle authority) already holds it at `deleted`. Everything
// else the console does stays Console-only (CP-8's "no per-deployment round
// trip" rule) — a second gateway call added here should be treated as a
// defect until a spec says otherwise.

import type { ConsoleResult, FetchLike } from "./console";

export type TenantDoorEnv = {
  url?: string;
  token?: string;
};

export class TenantDoorUnconfigured extends Error {}

const envTrim = (v: string | undefined): string | undefined => {
  const t = (v ?? "").trim();
  return t.length > 0 ? t : undefined;
};

export function readTenantDoorEnv(
  env: Record<string, string | undefined> = process.env,
): TenantDoorEnv {
  return {
    url: envTrim(env.GATEWAY_INTERNAL_URL),
    token: envTrim(env.GATEWAY_OPERATOR_TOKEN),
  };
}

// DELETE the organization's tenant plane. Fails CLOSED when unconfigured —
// a dark deployment refuses rather than skipping the tenant half silently
// (a "purge" that only stripped the registry would report success while the
// customer's data survived).
export async function purgeTenantOrg(
  slug: string,
  deps: { env?: TenantDoorEnv; fetchImpl?: FetchLike } = {},
): Promise<ConsoleResult> {
  const env = deps.env ?? readTenantDoorEnv();
  if (!env.url || !env.token) {
    throw new TenantDoorUnconfigured(
      "GATEWAY_INTERNAL_URL and GATEWAY_OPERATOR_TOKEN must both be set " +
        "server-side before the operator console can purge tenant data",
    );
  }
  const fetchImpl = deps.fetchImpl ?? (globalThis.fetch as unknown as FetchLike);
  const res = await fetchImpl(
    `${env.url}/internal/operator/organizations/${encodeURIComponent(slug)}` +
      `?confirm=${encodeURIComponent(slug)}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${env.token}` },
    },
  );
  return { status: res.status, body: await res.text() };
}
