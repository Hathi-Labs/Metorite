// POST /api/operator/members/remove — remove or delete one member, BOTH
// planes. Owner request, 2026-10-15.
//
// Body {org_slug, email, mode: "remove" | "purge", confirm, new_owner_email?},
// where confirm must equal the email verbatim. The order is the contract, and
// `route.test.ts` pins it:
//
//   0. The tenant door must be configured. If it is not, the route answers
//      503 and touches NOTHING. A Console-only removal would report success
//      while the person kept their access to the tenant.
//   1. Console `GET /operators/session` (and `GET /operators/elevate` for a
//      purge): the caller's rank. The gateway door trusts this console's
//      token, so the rank check must happen here, BEFORE the tenant act.
//   2. Console `GET /billing/summary`: the registry roster, for the owner
//      hand-over (owner decision, 2026-10-15: "the acting admin inherits").
//      The only active owner needs `new_owner_email`, an active member who is
//      not the target. A failed read refuses: without the roster, this route
//      cannot know whether the Console would refuse after the tenant act.
//   3. Gateway `DELETE /internal/operator/organizations/{slug}/members/{email}`:
//      the tenant plane. It goes FIRST of the two writes, because it is what
//      takes access away. `already_absent` counts as success, because the
//      registry half must still run (an invite that never reached the
//      tenant, or a retry).
//   4. Console `POST /registry/members/remove`: the registry plane. It marks
//      the membership removed, releases the seats and, with
//      `new_owner_email`, moves the billing ownership. It is idempotent, so a
//      retry after no answer is safe. A verdict is relayed, with no retry
//      advice (`memberRemove.registryFailure` holds the split).
//
// Every refusal that is this route's OWN carries `refused_by:
// "operator-console"`, and a gateway refusal carries `refused_by: "tenant"`.
// The roster's result line reads the mark (`memberRemove.refusedBy`).

import {
  billingSummary,
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
  OWN_REFUSAL,
  ownerHandover,
  readRemoveRequest,
  registryFailure,
  type ElevationRead,
} from "@/lib/memberRemove";
import { readMembers } from "@/lib/format";
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

// A refusal that is this route's own, marked so the roster names it right.
function own(status: number, body: Record<string, unknown>): Response {
  return json(status, { ...body, refused_by: OWN_REFUSAL });
}

export async function POST(request: Request): Promise<Response> {
  const admitted = await gate();
  if (!admitted.ok) return admitted.refusal;
  // ⚠️ The CALLER's session, forwarded to every Console call, as the org
  // purge does. The audit row then names a person.
  const d = { authToken: admitted.authToken };

  const parsed = readRemoveRequest(await readJsonBody(request));
  if (!parsed.ok) return own(400, { error: parsed.error });
  const { slug, email, mode, newOwner } = parsed.req;

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
    let elevation: ElevationRead | null = null;
    if (mode === "purge" && who.method === "session") {
      const win = await readElevation(d);
      if (win.status !== 200) return relay(win);
      elevation = readBody(win.body) as ElevationRead;
    }
    const refusal = mayRemove(who, mode, elevation);
    if (refusal !== null) return own(403, { error: refusal });

    // 2 — the registry roster, and the owner hand-over. Fail CLOSED.
    let summary;
    try {
      summary = await billingSummary(slug, d);
    } catch (e) {
      if (e instanceof ConsoleUnconfigured) throw e;
      return own(502, {
        error: "the Console did not answer the roster read. Nothing was changed.",
      });
    }
    if (summary.status !== 200) {
      return json(summary.status >= 500 ? 502 : summary.status, {
        error:
          `the Console's roster read answered ${summary.status}. ` +
          `Nothing was changed.`,
        console: readBody(summary.body),
      });
    }
    const raw = readBody(summary.body) as { members?: unknown } | null;
    if (raw === null || typeof raw !== "object" || !Array.isArray(raw.members)) {
      // A Console that predates the roster cannot tell us who the owners are.
      return own(502, {
        error:
          "the Console's roster read carried no member list, so the owner " +
          "check cannot run. Nothing was changed.",
      });
    }
    const handover = ownerHandover(readMembers(raw), email, newOwner);
    if (!handover.ok) {
      return own(400, { error: handover.error, needs_new_owner: true });
    }

    // 3 — the tenant plane.
    const tenant = await removeTenantMember(slug, email, mode);
    if (tenant.status !== 200) {
      return json(tenant.status, {
        error:
          `the tenant plane answered ${tenant.status} — nothing was changed ` +
          `on the registry`,
        refused_by: "tenant",
        tenant: readBody(tenant.body),
      });
    }

    // 4 — the registry plane.
    let registry;
    try {
      registry = await removeRegistryMember(
        {
          org_slug: slug,
          member_email: email,
          ...(newOwner ? { new_owner_email: newOwner } : {}),
        },
        d,
      );
    } catch (e) {
      if (e instanceof ConsoleUnconfigured) throw e;
      const f = registryFailure(email, null, null, slug);
      return json(f.status, {
        error: f.error,
        retry: f.retry,
        tenant: readBody(tenant.body),
      });
    }
    if (registry.status !== 200) {
      const detail = readBody(registry.body);
      const f = registryFailure(email, registry.status, detail, slug);
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
      return own(503, { error: "customer console is not configured" });
    }
    if (e instanceof TenantDoorUnconfigured) {
      return own(503, { error: DOOR_UNCONFIGURED });
    }
    throw e;
  }
}
