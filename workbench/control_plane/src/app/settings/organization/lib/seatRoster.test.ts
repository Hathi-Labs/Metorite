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
  interpretOverviewRead,
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
// The WRITE hops are the pre-existing gateway-backed pair. Slice 1 first shipped
// byte-identical twins under `/api/org/seats/{assign,release}` and they were
// deleted in review: one gateway route, one BFF file in front of it. If these
// paths stop resolving, the twins came back or the pair moved — either way this
// fence must be re-read rather than re-pointed.
const ASSIGN_ROUTE = code(source("../../../api/billing/seats/assign/route.ts"));
const RELEASE_ROUTE = code(source("../../../api/billing/seats/release/route.ts"));

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

describe("a refusal is not an outage — the read's five outcomes", () => {
  // The repair this block exists for (review round 1): every non-2xx that was
  // not 503 collapsed into "Could not read seats — the seat plane did not
  // answer". The Console 403s any actor who is not an active `owner|admin` in
  // its registry, and NO Console code path ever writes `role='admin'` (§6
  // CP-2f: the member-add door leaves `role` at the column default on purpose),
  // so the second tenant admin is a Console `member` and is 403 here. That made
  // "almost every admin except the founder" see a fabricated outage — and gave
  // whoever is on call a fabricated incident to chase.

  it("reads a 2xx as ready", () => {
    expect(interpretOverviewRead(200, { plans: [], members: [] })).toBe("ready");
    expect(interpretOverviewRead(204)).toBe("ready");
  });

  it("keeps 503 as the ONE unconfigured signal", () => {
    // The gateway answers 503 on an unwired box (`_unwired_read_refusal`) and
    // the BFF answers 503 when the gateway itself is unreachable. If this
    // stopped being distinguished, an unwired deployment would draw an empty
    // grid that looks like a confident answer.
    expect(interpretOverviewRead(503, { detail: "unavailable" })).toBe(
      "unconfigured",
    );
  });

  it("reads a 403 as the calm founder-only state, never an error", () => {
    // `_admin_scheme_for_deployment`'s two 403s, byte-identical on the wire:
    // no admissible org, and an admissible org where the actor's registry row
    // is not an active owner|admin. Both mean the plane ANSWERED.
    expect(
      interpretOverviewRead(403, {
        detail: "the acting member is not an active admin of this organization",
      }),
    ).toBe("restricted");
    expect(
      interpretOverviewRead(403, {
        detail: "the acting member is not an admin on this deployment",
      }),
    ).toBe("restricted");
  });

  it("reads the multi-org 409 as its own state", () => {
    // The Console's shape, re-derived from the gateway relay's fence
    // (`test_seat_admin_proxy_route.py::test_a_multi_org_409_surfaces_as_itself`)
    // and from `main.py` `_admin_scheme_for_deployment`: a bare `{detail: str}`.
    // CP-2h slice 2 threads the session org through and this state disappears.
    expect(
      interpretOverviewRead(409, {
        detail:
          "the acting member belongs to more than one organization on this " +
          "deployment; the organization cannot be inferred",
      }),
    ).toBe("ambiguous");
    // Status-classified, not phrase-matched: rewording the Console's sentence
    // must not silently turn this back into an outage banner.
    expect(interpretOverviewRead(409, { detail: "reworded upstream" })).toBe(
      "ambiguous",
    );
    expect(interpretOverviewRead(409, null)).toBe("ambiguous");
  });

  it("refuses to call a CAP 409 a multi-org 409", () => {
    // The sibling WRITE doors answer 409 with `{detail: {buy_more: …}}` and
    // relay through the same gateway. The read door makes no capacity decision,
    // so this shape should never arrive here — and if it does, "your email is
    // in two organizations" would be a confident lie. Degrade to `error`, which
    // claims only that we do not know what that was.
    expect(
      interpretOverviewRead(409, {
        detail: { reason: "no seats available", buy_more: { plan_slug: "core" } },
      }),
    ).toBe("error");
  });

  it("leaves every other non-2xx an error", () => {
    for (const status of [400, 401, 404, 429, 500, 502, 504]) {
      expect(interpretOverviewRead(status, { detail: "x" }), String(status)).toBe(
        "error",
      );
    }
  });
});

describe("the tab reads the GATEWAY's door, not the org-key billing hop", () => {
  it("fetches the overview from /api/org/seats and writes through the gateway pair", () => {
    // The slice, as a source scan: the NEW read hop, and the writes on the
    // pre-existing gateway-backed pair rather than a duplicate of it.
    expect(TAB).toContain('/api/org/seats"');
    expect(TAB).toContain("/api/billing/seats/${kind}");
  });

  it("never READS an org-key billing hop again — that is what was dark", () => {
    // The failure this fence exists for: `GET /api/billing/seats` and
    // `/api/billing/members` present a per-org `CUSTOMER_CONSOLE_ORG_KEY`
    // (`api/billing/_console.ts`), which cannot be correct on a shared
    // multi-tenant box. The env is unset there, the reads 503, and the tab shows
    // "not configured for this deployment" permanently. A reintroduced read
    // against either is that outage coming back.
    //
    // ⚠️ This is deliberately narrower than "no `/api/billing/` string at all":
    // `/api/billing/seats/{assign,release}` are NOT org-key routes — they are
    // `proxyToGateway` hops onto the same deployment-key door as the read, and
    // the scan below proves they carry no credential. Forbidding the prefix
    // wholesale is what pushed a byte-identical twin pair into `/api/org/`.
    expect(TAB).not.toContain('/api/billing/seats"');
    expect(TAB).not.toContain("/api/billing/members");
    expect(TAB).not.toContain("_console");
  });

  it("delegates the read outcome to the testable classifier", () => {
    // The status→state judgement lives in `interpretOverviewRead`, not inline
    // in the fetch callback: a branch inside a `useCallback` is unreachable by
    // any test in this tree, and it stayed wrong for exactly that reason.
    expect(TAB).toContain("interpretOverviewRead(r.status, payload)");
    expect(TAB).not.toMatch(/status === 503/);
  });

  it("draws every answered state, and only the unknown one in red", () => {
    // Each `SeatPlaneRead` needs a branch or the state renders as an empty
    // roster. `tone="warning"` is the red-ish one and belongs to `error` alone.
    for (const state of ["unconfigured", "restricted", "ambiguous", "error"]) {
      expect(TAB, state).toContain(`plane === "${state}"`);
    }
    expect(TAB.match(/tone="warning"/g) ?? []).toHaveLength(1);
  });
});

describe("the hops name no tenant and hold no credential (R11)", () => {
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
