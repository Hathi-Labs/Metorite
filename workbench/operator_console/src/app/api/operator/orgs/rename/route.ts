// POST /api/operator/orgs/rename — rename one organization, BOTH planes.
// CP-8 "Rename an organization" (owner request, 2026-10-27).
//
// Body {org_slug, name}. The slug does not change. The order is the
// contract, and `route.test.ts` pins it:
//
//   0. The tenant door must be configured. If it is not, the route answers
//      503 and touches NOTHING. A Console-only rename would report success
//      while the customer's app kept the old name.
//   1. Console `GET /operators/session`: the caller's rank. Rename needs
//      EDITOR. The gateway door trusts this console's token, so the rank
//      check must happen here, BEFORE the tenant act.
//   2. Gateway `PATCH /internal/operator/organizations/{slug}`: the tenant
//      plane. It goes FIRST of the two writes, as in member removal. Its
//      `clean_display_name` is the one name check, so an empty, long or
//      unprintable name comes back as its 422.
//   3. Console `POST /registry/orgs/rename`: the registry plane. It gets the
//      name the tenant plane stored, so both planes hold one value. A retry
//      is safe, because both writes set the same value.
//
// A refusal that is this route's OWN carries `refused_by:
// "operator-console"`, and a gateway refusal carries `refused_by: "tenant"`.
// The panel's result line reads the mark (`memberRemove.refusedBy`).

import {
  ConsoleUnconfigured,
  readOperatorSession,
  renameRegistryOrg,
} from "@/lib/console";
import {
  renameTenantOrg,
  tenantDoorConfigured,
  TenantDoorUnconfigured,
} from "@/lib/tenantDoor";
import { OWN_REFUSAL } from "@/lib/memberRemove";
import {
  mayRename,
  readRenameRequest,
  registryRenameFailure,
  storedName,
} from "@/lib/orgRename";
import { readWhoami } from "@/lib/whoami";
import { gate, json, readJsonBody, relay } from "@/lib/route";

export const dynamic = "force-dynamic";

const DOOR_UNCONFIGURED =
  "the gateway operator door is not configured — set the gateway URL, " +
  "internal token and operator-door token in the console's server env. " +
  "Nothing was changed.";

// A body we can show: the parsed JSON, or the raw text when it is not JSON.
function readBody(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

// A refusal that is this route's own, marked so the panel names it right.
function own(status: number, body: Record<string, unknown>): Response {
  return json(status, { ...body, refused_by: OWN_REFUSAL });
}

export async function POST(request: Request): Promise<Response> {
  const admitted = await gate();
  if (!admitted.ok) return admitted.refusal;
  // ⚠️ The CALLER's session, forwarded to every Console call. The audit row
  // then names a person.
  const d = { authToken: admitted.authToken };

  const parsed = readRenameRequest(await readJsonBody(request));
  if (!parsed.ok) return own(400, { error: parsed.error });
  const { slug, name } = parsed.req;

  // 0 — fail closed before any upstream call.
  if (!tenantDoorConfigured()) return own(503, { error: DOOR_UNCONFIGURED });

  try {
    // 1 — the caller's rank.
    const session = await readOperatorSession(d);
    if (session.status !== 200) return relay(session);
    const who = readWhoami(readBody(session.body));
    if (who === null) {
      return own(502, {
        error: "the Console's session read gave a shape this console cannot read",
      });
    }
    const refusal = mayRename(who);
    if (refusal !== null) return own(403, { error: refusal });

    // 2 — the tenant plane.
    const tenant = await renameTenantOrg(slug, name);
    const tenantBody = readBody(tenant.body);
    if (tenant.status !== 200) {
      return json(tenant.status, {
        error:
          `the tenant plane answered ${tenant.status} — nothing was changed ` +
          `on the registry`,
        refused_by: "tenant",
        tenant: tenantBody,
      });
    }

    // 3 — the registry plane, with the name the tenant plane stored.
    let registry;
    try {
      registry = await renameRegistryOrg(
        { org_slug: slug, name: storedName(tenantBody, name) },
        d,
      );
    } catch (e) {
      if (e instanceof ConsoleUnconfigured) throw e;
      const f = registryRenameFailure(null, null);
      return json(f.status, { error: f.error, retry: f.retry, tenant: tenantBody });
    }
    if (registry.status !== 200) {
      const detail = readBody(registry.body);
      const f = registryRenameFailure(registry.status, detail);
      return json(f.status, {
        error: f.error,
        retry: f.retry,
        tenant: tenantBody,
        registry: detail,
      });
    }

    return json(200, { tenant: tenantBody, registry: readBody(registry.body) });
  } catch (e) {
    if (e instanceof ConsoleUnconfigured) {
      return own(503, { error: "customer console is not configured" });
    }
    if (e instanceof TenantDoorUnconfigured) {
      return own(503, { error: DOOR_UNCONFIGURED });
    }
    throw e;
  }
}
