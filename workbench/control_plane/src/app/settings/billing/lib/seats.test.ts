/**
 * SC-1a's seats block — the three properties its done-when names, fenced.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-1a (the launch SEATS
 * BLOCK) · `customer_console.md` §6 item (g) / done-when 19.
 *
 * The done-when has three testable claims, and this file pins each:
 *
 *   1. **The four rendered numbers equal the read's payload** — for the named
 *      fixture (purchased 3 / assigned 1 / available 2) and for an
 *      oversubscribed case. Proved by feeding a payload through the same pure
 *      accessors the block renders through and comparing verbatim.
 *   2. **The block holds NO seat arithmetic of its own** — `oversubscribed` is
 *      the server's FLAG, surfaced explicitly, never re-derived from a clamped
 *      `available == 0`; and neither the view-model nor the page recomputes a
 *      count. Proved behaviourally (an inconsistent row survives untouched) and
 *      by a source scan of the page block.
 *   3. **The hop forwards no client-supplied org** — identity is resolved
 *      server-side and the organization is a property of the deployment's key,
 *      never a query parameter, body or header the browser can name (R11).
 *      Proved by a source scan of the route.
 *
 * The two-org half of the done-when — "org A its own rows and never org B's" —
 * is the CONSOLE read's property (`GET /me/seats` takes the org from
 * `caller.organization_id`, fenced by `test_the_seats_read_is_scoped_to_the_key`)
 * and this surface's half of it is claim 3: the browser cannot name a tenant, so
 * it can only ever be shown its own.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  type SeatPlan,
  SEAT_COUNTS,
  isOversubscribed,
  seatCells,
} from "./seats";

function source(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf-8");
}

/** The file with comments removed — a scan is about what the code DOES. */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const PAGE = code(source("../page.tsx"));
const SEATS = code(source("./seats.ts"));
const ROUTE = code(source("../../../api/billing/seats/route.ts"));

function plan(over: Partial<SeatPlan> = {}): SeatPlan {
  return {
    plan_slug: "core",
    purchased: 3,
    assigned: 1,
    available: 2,
    oversubscribed: false,
    ...over,
  };
}

// ---------------------------------------------------------------------------
// 1. The four numbers equal the read's payload
// ---------------------------------------------------------------------------

describe("done-when — the rendered numbers ARE the read's payload", () => {
  it("shows purchased 3 / assigned 1 / available 2 for the named fixture", () => {
    expect(seatCells(plan())).toEqual([
      { label: "Purchased", value: 3 },
      { label: "Assigned", value: 1 },
      { label: "Available", value: 2 },
    ]);
  });

  it("names the three counts in read order and nothing derived", () => {
    expect(SEAT_COUNTS.map((c) => c.key)).toEqual([
      "purchased",
      "assigned",
      "available",
    ]);
  });

  it("passes the payload through — a different fixture yields different numbers", () => {
    expect(seatCells(plan({ purchased: 9, assigned: 4, available: 5 }))).toEqual([
      { label: "Purchased", value: 9 },
      { label: "Assigned", value: 4 },
      { label: "Available", value: 5 },
    ]);
  });
});

// ---------------------------------------------------------------------------
// 2. No seat arithmetic of its own
// ---------------------------------------------------------------------------

describe("done-when — the block holds no seat arithmetic", () => {
  it("surfaces `oversubscribed` from the FLAG, not from a clamped available==0", () => {
    // The exact case SC-1a names: available clamped to zero WITHOUT
    // oversubscription, and oversubscription WITH available above zero. A block
    // that read `available === 0` as "oversubscribed" would get both wrong.
    expect(isOversubscribed(plan({ available: 0, oversubscribed: false }))).toBe(
      false,
    );
    expect(isOversubscribed(plan({ available: 5, oversubscribed: true }))).toBe(
      true,
    );
  });

  it("renders an internally inconsistent row verbatim rather than 'fixing' it", () => {
    // A computing implementation would clamp `available` to max(0, purchased -
    // assigned) = 0; returning 9 proves nothing here recomputes it.
    expect(
      seatCells(plan({ purchased: 2, assigned: 5, available: 9 })).map(
        (c) => c.value,
      ),
    ).toEqual([2, 5, 9]);
  });

  it("keeps the view-model free of arithmetic", () => {
    expect(SEATS).not.toMatch(/Math\./);
    // No re-derivation of a count from the others, in either direction.
    expect(SEATS).not.toMatch(/purchased\s*-\s*assigned/);
    expect(SEATS).not.toMatch(/assigned\s*-\s*purchased/);
    expect(SEATS).not.toMatch(/assigned\s*>\s*purchased/);
  });

  it("renders the counts through the descriptor and the flag through the field", () => {
    // The page reads each count by key off `SEAT_COUNTS` and the oversubscribed
    // state off the read's flag — never a computed value.
    expect(PAGE).toContain("SEAT_COUNTS");
    expect(PAGE).toMatch(/isOversubscribed|\.oversubscribed/);
  });

  it("does not recompute a seat count on the page", () => {
    expect(PAGE).not.toMatch(/Math\.max/);
    expect(PAGE).not.toMatch(/purchased\s*-\s*assigned/);
    expect(PAGE).not.toMatch(/assigned\s*-\s*purchased/);
    expect(PAGE).not.toMatch(/assigned\s*>\s*purchased/);
    // The oversubscribed state must not be faked from a clamped available.
    expect(PAGE).not.toMatch(/available\s*===?\s*0/);
    expect(PAGE).not.toMatch(/available\s*<=?\s*0/);
  });

  it("guards the seats render against a non-array payload (no whole-page crash)", () => {
    // A malformed Console 2xx can relay as `{}` (relayConsole returns `body ?? {}`);
    // the render-gate must not dereference `.plans.length` on a non-array, or it
    // white-screens the whole billing page. Pin the array guard.
    expect(PAGE).toContain("Array.isArray(seats?.plans)");
  });
});

// ---------------------------------------------------------------------------
// 3. The hop forwards no client-supplied org
// ---------------------------------------------------------------------------

describe("done-when — the seats hop takes no org from the caller", () => {
  it("resolves identity server-side and pins the org to the deployment's key", () => {
    // Identity is the session's, and the organization is a property of the
    // credential `consoleHeaders`/`consoleConfig` present — never the browser's.
    expect(ROUTE).toContain("currentIdentity");
    expect(ROUTE).toContain("consoleConfig");
    expect(ROUTE).toContain("consoleHeaders");
    expect(ROUTE).toContain("/me/seats");
  });

  it("reads no organization, and no request input at all, off the wire", () => {
    // No body, no query, no route params, no caller-named tenant. The GET
    // handler takes no argument for exactly this reason.
    expect(ROUTE).not.toMatch(/searchParams/);
    expect(ROUTE).not.toMatch(/req(uest)?\.(json|url|headers|nextUrl)/);
    expect(ROUTE).not.toMatch(/\borg_slug\b/);
    expect(ROUTE).not.toMatch(/params/);
    expect(ROUTE).toMatch(/async function GET\(\)/);
  });
});
