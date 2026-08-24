/**
 * The seat roster's fence (R7 · `launch_surface.md` LS-7 / §6.2), and — since
 * CP-2h slice 1 — the **D-SEAT-4 reroute's** frontend half.
 *
 * The claim worth pinning, and the reason the module exists:
 *
 * > Releasing somebody's seat must not remove them from the screen that offers
 * > to give it back.
 *
 * The claim CP-2h adds, and the reason the surface was dark:
 *
 * > The Seats tab must work on a SHARED deployment with no per-org env — so it
 * > reads the GATEWAY's deployment-key door, never the org-key billing hop.
 *
 * Everything else here defends a case that would otherwise be invisible until
 * it happened to one real colleague.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { Member } from "@/app/settings/members/types";
import type { Member as BillingMember } from "@/app/settings/billing/lib/manage";
import type { SeatPlan } from "@/app/settings/billing/lib/seats";

import {
  buildSeatRows,
  canOfferSeat,
  isSeated,
  readSeatOverview,
  tally,
} from "./seatRoster";

function source(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf-8");
}

/** The file with comments removed — a scan is about what the code DOES. */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const member = (over: Partial<Member> = {}): Member => ({
  email: "priya@fracktal.in",
  display_name: "Priya",
  status: "active",
  roles: ["member"],
  ...over,
});

const billed = (email: string, seats: string[]): BillingMember => ({
  email,
  role: "member",
  status: "active",
  seats,
});

describe("who appears on the seat surface", () => {
  it("lists a member the billing plane has never heard of, as Unassigned", () => {
    // A freshly invited colleague. If the surface were driven by the Console's
    // roster they would simply be absent — and absent is the one state from
    // which an admin cannot assign them a seat.
    const rows = buildSeatRows([member({ email: "new@fracktal.in" })], []);
    expect(rows).toHaveLength(1);
    expect(isSeated(rows[0])).toBe(false);
    expect(rows[0].unknownToBilling).toBe(true);
    expect(canOfferSeat(rows[0])).toBe(true);
  });

  it("keeps a RELEASED member on the list, Unassigned and reassignable", () => {
    // D49's second bullet, as a test. The Console keeps the released row (with
    // `released_at` set) and reports `seats: []` for them; the surface must
    // read that as "here, and needs a seat", never as "gone".
    const rows = buildSeatRows(
      [member()],
      [billed("priya@fracktal.in", [])],
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].seats).toEqual([]);
    expect(isSeated(rows[0])).toBe(false);
    // NOT unknown: billing knows them, they just hold nothing. The distinction
    // exists so the surface can avoid implying a seat was taken from somebody
    // who never had one.
    expect(rows[0].unknownToBilling).toBe(false);
    expect(canOfferSeat(rows[0])).toBe(true);
  });

  it("marks a seated member seated, carrying the slug", () => {
    const rows = buildSeatRows(
      [member()],
      [billed("priya@fracktal.in", ["core"])],
    );
    expect(isSeated(rows[0])).toBe(true);
    expect(rows[0].seats).toEqual(["core"]);
  });

  it("matches emails case-insensitively", () => {
    // The Console stores email as CITEXT; the gateway does not. A
    // case-sensitive join would report a seated member as Unassigned — for
    // exactly one colleague, the one who capitalised their address.
    const rows = buildSeatRows(
      [member({ email: "Priya@Fracktal.in" })],
      [billed("priya@fracktal.in", ["core"])],
    );
    expect(isSeated(rows[0])).toBe(true);
    // The DISPLAYED address stays the gateway's, so the two tabs agree.
    expect(rows[0].email).toBe("Priya@Fracktal.in");
  });

  it("preserves the gateway roster's order and its people, exactly", () => {
    // Same people, same order, as the Members tab — an admin moving between
    // tabs should not have to re-find anybody. And the billing roster cannot
    // ADD a row: somebody in the billing plane but not the org directory is not
    // a member of this organization.
    const members = [
      member({ email: "a@x.test" }),
      member({ email: "b@x.test" }),
      member({ email: "c@x.test" }),
    ];
    const rows = buildSeatRows(members, [billed("zz@x.test", ["core"])]);
    expect(rows.map((r) => r.email)).toEqual(["a@x.test", "b@x.test", "c@x.test"]);
  });

  it("treats an unreachable Console as unknown, not as nobody-has-a-seat", () => {
    // `null` is "we could not ask". Every row is Unassigned AND flagged
    // unknown, so the surface can say the seat plane is unreachable instead of
    // drawing an empty grid that looks like a confident answer.
    const rows = buildSeatRows([member(), member({ email: "b@x.test" })], null);
    expect(rows.every((r) => r.unknownToBilling)).toBe(true);
    expect(rows.every((r) => !isSeated(r))).toBe(true);
  });

  it("survives a Console row whose seats field is missing or malformed", () => {
    // A Console predating LS-7 omits the field; `relayConsole` can pass a
    // malformed 2xx through. Neither may white-screen the page.
    const rows = buildSeatRows(
      [member(), member({ email: "b@x.test" })],
      [
        { email: "priya@fracktal.in", role: "member", status: "active" },
        {
          email: "b@x.test",
          role: "member",
          status: "active",
          seats: "core" as unknown as string[],
        },
      ],
    );
    expect(rows[0].seats).toEqual([]);
    expect(rows[1].seats).toEqual([]);
  });
});

describe("what the admin is offered", () => {
  it("offers a seat to an INVITED member — that is the onboarding order", () => {
    // Seating a colleague before their first sign-in is the point of the
    // invite→assign flow (D49's first bullet), not an edge case.
    expect(canOfferSeat(buildSeatRows([member({ status: "invited" })], [])[0])).toBe(
      true,
    );
  });

  it("still offers a seat to a SUSPENDED member", () => {
    // Holding a suspended colleague's seat is a real, chosen state — the org is
    // paying to keep their place. The remedy for the other case is Release,
    // which is on the same row.
    expect(
      canOfferSeat(buildSeatRows([member({ status: "suspended" })], [])[0]),
    ).toBe(true);
  });

  it("does not offer a seat to a REMOVED member", () => {
    // Capacity spent on nobody.
    expect(canOfferSeat(buildSeatRows([member({ status: "removed" })], [])[0])).toBe(
      false,
    );
  });
});

describe("the tally counts ROWS, and says so", () => {
  it("splits seated from unassigned across the whole roster", () => {
    const rows = buildSeatRows(
      [
        member({ email: "a@x.test" }),
        member({ email: "b@x.test" }),
        member({ email: "c@x.test" }),
      ],
      [billed("a@x.test", ["core"]), billed("b@x.test", [])],
    );
    expect(tally(rows)).toEqual({ total: 3, seated: 1, unassigned: 2 });
  });

  it("counts a multi-plan holder ONCE — these are people, not seats", () => {
    // The trap this guards: presenting `seated` as the seat count. One person
    // on two plans is one seated row and two assigned seats, and the surface
    // must take the second number from the Console's grid, never from here.
    const rows = buildSeatRows(
      [member({ email: "a@x.test" })],
      [billed("a@x.test", ["core", "qa-second-seat"])],
    );
    expect(tally(rows)).toEqual({ total: 1, seated: 1, unassigned: 0 });
  });
});

// ---------------------------------------------------------------------------
// CP-2h slice 1 — the D-SEAT-4 reroute
// ---------------------------------------------------------------------------

const TAB = code(source("../SeatsTab.tsx"));
const OVERVIEW_ROUTE = code(source("../../../api/org/seats/route.ts"));
const ASSIGN_ROUTE = code(source("../../../api/org/seats/assign/route.ts"));
const RELEASE_ROUTE = code(source("../../../api/org/seats/release/route.ts"));

const plan = (over: Partial<SeatPlan> = {}): SeatPlan => ({
  plan_slug: "core",
  purchased: 3,
  assigned: 1,
  available: 2,
  oversubscribed: false,
  ...over,
});

describe("the overview payload is read, never reshaped", () => {
  it("passes both halves through verbatim", () => {
    const payload = {
      plans: [plan()],
      members: [billed("priya@fracktal.in", ["core"])],
    };
    expect(readSeatOverview(payload)).toEqual(payload);
  });

  it("guards a malformed 2xx on EITHER half rather than white-screening", () => {
    // The hop relays whatever the Console said. A non-array `plans` would crash
    // the counts block on `.map` — the same trap `readMembers` already guards
    // one field along, which is why both are guarded and not just the roster.
    expect(readSeatOverview(null)).toEqual({ plans: [], members: [] });
    expect(readSeatOverview({})).toEqual({ plans: [], members: [] });
    expect(
      readSeatOverview({ plans: "oops" as unknown as SeatPlan[] }),
    ).toEqual({ plans: [], members: [] });
    expect(
      readSeatOverview({ members: "oops" as unknown as BillingMember[] }),
    ).toEqual({ plans: [], members: [] });
  });

  it("computes no count — an inconsistent row survives untouched", () => {
    // The ONE seat vocabulary (D32.5): `available`'s clamp and `oversubscribed`
    // are the Console's. A client that "fixed" them would be the second source
    // of truth this whole surface exists to avoid.
    const odd = plan({ purchased: 2, assigned: 5, available: 0, oversubscribed: true });
    expect(readSeatOverview({ plans: [odd] }).plans[0]).toEqual(odd);
  });
});

describe("the tab reads the GATEWAY's door, not the org-key billing hop", () => {
  it("fetches the overview and posts the writes under /api/org/seats", () => {
    // The whole slice, as a source scan: three endpoints, all on the org
    // namespace, all reaching the Console through the gateway's per-BOX
    // deployment key.
    expect(TAB).toContain('/api/org/seats"');
    expect(TAB).toContain("/api/org/seats/${kind}");
  });

  it("reads NOTHING from /api/billing any more — that is what was dark", () => {
    // The failure this fence exists for: `/api/billing/{seats,members}` present
    // a per-org `CUSTOMER_CONSOLE_ORG_KEY`, which cannot be correct on a shared
    // multi-tenant box. The env is unset there, the reads 503, and the tab shows
    // "not configured for this deployment" permanently. A reintroduced
    // `/api/billing/...` fetch here is that outage coming back.
    expect(TAB).not.toContain("/api/billing/");
  });

  it("keeps 503 as the ONE unconfigured signal", () => {
    // `PlaneState` distinguishes an unreachable seat plane from an empty one,
    // and 503 is the status it keys on. If the tab stopped branching on it, an
    // unwired deployment would render an empty grid that looks like a
    // confident answer.
    expect(TAB).toMatch(/status === 503/);
    expect(TAB).toContain('setPlane("unconfigured")');
  });
});

describe("the three hops name no tenant and hold no credential (R11)", () => {
  const ROUTES: ReadonlyArray<[string, string]> = [
    ["overview", OVERVIEW_ROUTE],
    ["assign", ASSIGN_ROUTE],
    ["release", RELEASE_ROUTE],
  ];

  it("forwards through the single gateway door", () => {
    // `proxyToGateway` is the only module that may mint the internal bearer and
    // it attaches the signed-in member's identity; a hop that built its own
    // headers would be free to omit the identity, which `lib/gateway.ts`'s
    // header records as a PRIVILEGE ESCALATION rather than a downgrade.
    for (const [name, src] of ROUTES) {
      expect(src, name).toContain("proxyToGateway(");
      expect(src, name).toContain('export const dynamic = "force-dynamic"');
    }
  });

  it("holds no Console URL and no Console key", () => {
    // The deployment key is fenced OUT of the Next/browser tier by name
    // (`customer_console.md` §6(f)); the org key belongs to the billing hops.
    // A hop here naming either has moved a credential into the wrong tier.
    for (const [name, src] of ROUTES) {
      expect(src, name).not.toContain("CUSTOMER_CONSOLE_DEPLOYMENT_KEY");
      expect(src, name).not.toContain("CUSTOMER_CONSOLE_ORG_KEY");
      expect(src, name).not.toContain("CUSTOMER_CONSOLE_URL");
    }
  });

  it("never forwards an org or an actor the browser could name", () => {
    // R11: the acting admin is the SESSION (the gateway reads `X-User-Email`)
    // and the org is derived Console-side. The write hops rebuild the outbound
    // body from the three allowed fields; the read hop sends no body at all.
    for (const [name, src] of ROUTES) {
      expect(src, name).not.toMatch(/\borg_slug\b/);
      expect(src, name).not.toMatch(/\bactor_email\b/);
    }
    for (const [name, src] of [ROUTES[1], ROUTES[2]]) {
      expect(src, name).toContain("member_email: raw.member_email");
      expect(src, name).toContain("plan_slug: raw.plan_slug");
      // A spread of the browser's body would carry whatever it sent.
      expect(src, name).not.toMatch(/\.\.\.raw/);
    }
  });

  it("answers 503 when the gateway itself is unreachable", () => {
    // One hop earlier than the gateway's own 503, and the same fact, so the
    // surface's "unconfigured" state keeps meaning what it says instead of
    // flipping to the red error banner.
    expect(OVERVIEW_ROUTE).toContain("status: 503");
  });
});
