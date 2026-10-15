// POST /api/operator/members/remove — remove or delete one member, BOTH
// planes. Owner request, 2026-10-15.
//
// Body {org_slug, email, mode: "remove" | "purge", confirm}, where confirm
// must equal the email verbatim. The order is the contract, and
// `route.test.ts` pins it:
//
//   0. The tenant door must be configured. If it is not, the route answers
//      503 and touches NOTHING. A Console-only removal would report success
//      while the person kept their access to the tenant.
//   1. Console `GET /operators/session` (and `GET /operators/elevate` for a
//      purge): the caller's rank. The gateway door trusts this console's
//      token, so the rank check must happen here, BEFORE the tenant act.
//   2. Gateway `DELETE /internal/operator/organizations/{slug}/members/{email}`:
//      the tenant plane. It goes FIRST because it is what takes access away.
//      `already_absent` counts as success, because the registry half must
//      still run (an invite that never reached the tenant, or a retry).
//   3. Console `POST /registry/members/remove`: the registry plane. It marks
//      the membership removed and releases the seats. It is idempotent, so a
//      retry after a fault on the way (no answer, a 5xx) is safe. A 4xx is
//      the Console's verdict: its status is relayed, with no retry advice.

import {
  ConsoleUnconfigured,
  readElevation,
  readOperatorSession,
  removeRegistryMember,
} from "@/lib/console";
import {
  removeTenantMember,
  tenantDoorConfigured,
  TenantDoorUnconfigured,
} from "@/lib/tenantDoor";
import {
  mayRemove,
  readRemoveRequest,
  registryFailure,
  type ElevationRead,
} from "@/lib/memberRemove";
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

export async function POST(request: Request): Promise<Response> {
  const admitted = await gate();
  if (!admitted.ok) return admitted.refusal;
  // ⚠️ The CALLER's session, forwarded to every Console call, as the org
  // purge does. The audit row then names a person.
  const d = { authToken: admitted.authToken };

  const parsed = readRemoveRequest(await readJsonBody(request));
  if (!parsed.ok) return json(400, { error: parsed.error });
  const { slug, email, mode } = parsed.req;

  // 0 — fail closed before any upstream call.
  if (!tenantDoorConfigured()) return json(503, { error: DOOR_UNCONFIGURED });

  try {
    // 1 — the caller's rank.
    const session = await readOperatorSession(d);
    if (session.status !== 200) return relay(session);
    const who = readWhoami(readBody(session.body));
    if (who === null) {
      return json(502, {
        error: "the Console's session read gave a shape this console cannot read",
      });
    }
    let elevation: ElevationRead | null = null;
    if (mode === "purge" && who.method === "session") {
      const win = await readElevation(d);
      if (win.status !== 200) return relay(win);
      elevation = readBody(win.body) as ElevationRead;
    }
    const refusal = mayRemove(who, mode, elevation);
    if (refusal !== null) return json(403, { error: refusal });

    // 2 — the tenant plane.
    const tenant = await removeTenantMember(slug, email, mode);
    if (tenant.status !== 200) {
      return json(tenant.status, {
        error:
          `the tenant plane answered ${tenant.status} — nothing was changed ` +
          `on the registry`,
        // The roster reads this to name the tenant plane as the refuser
        // (`memberRemove.refusedBy`), not the Console.
        refused_by: "tenant",
        tenant: readBody(tenant.body),
      });
    }

    // 3 — the registry plane. A fault on the way (no answer, a 5xx) gets 502
    // and the retry. A Console verdict (a 4xx) is relayed with its own status
    // and no retry (`registryFailure` holds the split).
    let registry;
    try {
      registry = await removeRegistryMember(
        { org_slug: slug, member_email: email },
        d,
      );
    } catch (e) {
      if (e instanceof ConsoleUnconfigured) throw e;
      const f = registryFailure(email, null, null);
      return json(f.status, {
        error: f.error,
        retry: f.retry,
        tenant: readBody(tenant.body),
      });
    }
    if (registry.status !== 200) {
      const detail = readBody(registry.body);
      const f = registryFailure(email, registry.status, detail);
      return json(f.status, {
        error: f.error,
        retry: f.retry,
        tenant: readBody(tenant.body),
        registry: detail,
      });
    }

    return json(200, {
      tenant: readBody(tenant.body),
      registry: readBody(registry.body),
    });
  } catch (e) {
    if (e instanceof ConsoleUnconfigured) {
      return json(503, { error: "customer console is not configured" });
    }
    if (e instanceof TenantDoorUnconfigured) {
      return json(503, { error: DOOR_UNCONFIGURED });
    }
    throw e;
  }
}
