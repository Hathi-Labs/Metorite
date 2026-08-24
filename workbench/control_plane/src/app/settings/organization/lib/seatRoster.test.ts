/**
 * The seat roster's fence (R7 · `launch_surface.md` LS-7 / §6.2).
 *
 * The claim worth pinning, and the reason the module exists:
 *
 * > Releasing somebody's seat must not remove them from the screen that offers
 * > to give it back.
 *
 * Everything else here defends a case that would otherwise be invisible until
 * it happened to one real colleague.
 */

import { describe, expect, it } from "vitest";

import type { Member } from "@/app/settings/members/types";
import type { Member as BillingMember } from "@/app/settings/billing/lib/manage";

import { buildSeatRows, canOfferSeat, isSeated, tally } from "./seatRoster";

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
    // must take the second number from `GET /me/seats`, never from here.
    const rows = buildSeatRows(
      [member({ email: "a@x.test" })],
      [billed("a@x.test", ["core", "qa-second-seat"])],
    );
    expect(tally(rows)).toEqual({ total: 1, seated: 1, unassigned: 0 });
  });
});
