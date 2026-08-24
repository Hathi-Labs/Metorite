// POST /api/operator/purge — destroy an organization, BOTH planes. (CP-2g)
//
// The one BFF route that touches two upstreams, in a fixed order the tests
// pin (`route.test.ts`):
//
//   1. Console `GET /orgs` — the AUTHORITY check: the org must already be
//      `deleted` on the lifecycle graph. `deleted` is terminal (nothing
//      transitions out of it), so the check cannot be invalidated between
//      here and step 3.
//   2. Gateway `DELETE /internal/operator/organizations/{slug}` — the tenant
//      plane. Idempotent (`already_absent` on a retry).
//   3. Console `POST /orgs/purge` — the registry plane; re-checks `deleted`
//      itself, strips personal data, tombstone-renames the slug.
//
// Tenant before registry, deliberately: the registry purge renames the slug,
// and the gateway door is addressed BY slug — the other order would strand
// the tenant data under a name nobody can type. A failure between 2 and 3
// leaves the Console row intact and the whole action retryable as-is.

import { listOrganizations, purgeOrgRegistry } from "@/lib/console";
import { purgeTenantOrg, TenantDoorUnconfigured } from "@/lib/tenantDoor";
import { gateStaff, json, readJsonBody } from "@/lib/route";
import { ConsoleUnconfigured } from "@/lib/console";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const refusal = await gateStaff();
  if (refusal) return refusal;

  const body = (await readJsonBody(request)) as {
    org_slug?: string;
    confirm?: string;
  };
  const slug = (body.org_slug ?? "").trim();
  const confirm = (body.confirm ?? "").trim();
  if (!slug) return json(400, { error: "org_slug is required" });
  if (confirm !== slug) {
    return json(400, {
      error: "confirm must equal the organization slug, verbatim",
    });
  }

  try {
    // 1 — the authority check.
    const list = await listOrganizations();
    if (list.status !== 200) {
      return json(502, {
        error: `the Console's org list answered ${list.status}`,
        console: list.body,
      });
    }
    const orgs = (JSON.parse(list.body) as {
      organizations: { slug: string; status: string }[];
    }).organizations;
    const org = orgs.find((o) => o.slug === slug);
    if (!org) return json(404, { error: `no organization "${slug}"` });
    if (org.status !== "deleted") {
      return json(409, {
        error:
          `organization is "${org.status}"; purge is reachable only in ` +
          `"deleted". The path is: cancel access (opens the export window), ` +
          `then mark deleted, then purge.`,
      });
    }

    // 2 — the tenant plane.
    const tenant = await purgeTenantOrg(slug);
    if (tenant.status !== 200) {
      return json(502, {
        error:
          `the tenant purge answered ${tenant.status} — nothing was ` +
          `destroyed on the registry; fix the cause and run this again`,
        tenant: tenant.body,
      });
    }

    // 3 — the registry plane.
    const registry = await purgeOrgRegistry({ org_slug: slug, confirm });
    if (registry.status !== 200) {
      return json(502, {
        error:
          `tenant data is destroyed but the registry purge answered ` +
          `${registry.status} — run this action again to finish ` +
          `(the tenant half answers already_absent on the retry)`,
        registry: registry.body,
      });
    }

    return json(200, {
      tenant: JSON.parse(tenant.body),
      registry: JSON.parse(registry.body),
    });
  } catch (e) {
    if (e instanceof ConsoleUnconfigured) {
      return json(503, { error: "customer console is not configured" });
    }
    if (e instanceof TenantDoorUnconfigured) {
      return json(503, {
        error:
          "the gateway operator door is not configured — set the gateway " +
          "URL and operator-door token in the console's server env",
      });
    }
    throw e;
  }
}
