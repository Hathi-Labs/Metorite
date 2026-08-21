/**
 * SC-2b's manage-seats UI — its done-whens, fenced.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2b · `customer_console.md`
 * §6 items (h)/(i) · SC-2a (the Next hops driven) · R11.
 *
 * The done-when has five testable claims, and this file pins each:
 *
 *   1. **The roster IS `GET /me/members`'s rows** — `readMembers` passes the
 *      read's `members` through verbatim and guards a malformed 2xx (a Console
 *      `{}`) so a non-array cannot white-screen the page.
 *   2. **The controls POST the right bodies to the right hops** — `assignBody`
 *      names `{member_email, plan_slug, source}` and `releaseBody` drops
 *      `source` (R11: nothing else), and the page posts them to
 *      `/api/billing/seats/{assign,release}`.
 *   3. **No seat arithmetic of its own** — neither the view-model nor the page
 *      recomputes a count; `available` is read as a boolean gate, never
 *      re-derived, and the counts come from the read.
 *   4. **On success the SC-1a read is refetched** — the page threads
 *      `onChanged={loadSeats}` and awaits it on an `ok`, so `assigned+1 /
 *      available−1` is the READ's next answer, not a local adjustment.
 *   5. **The cap 409 renders `buy_more` verbatim** — `interpretSeatAction`
 *      carries the server's payload untouched and `buyMoreMessage` builds the
 *      copy from the server's OWN numbers; an idempotent already-assigned write
 *      is a 200 `ok` (server-side), never a special case here.
 *
 * The surface gate is `billing:purchase` (claim: a non-admin sees no controls),
 * asserted by a source scan of the page.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import type { SeatPlan } from "./seats";
import {
  type BuyMore,
  type Member,
  type MembersPayload,
  ASSIGN_SOURCE,
  assignBody,
  buyMoreMessage,
  canAssign,
  interpretSeatAction,
  readMembers,
  releaseBody,
} from "./manage";

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
const MANAGE = code(source("./manage.ts"));

function plan(over: Partial<SeatPlan> = {}): SeatPlan {
  return {
    plan_slug: "center-ops",
    purchased: 3,
    assigned: 1,
    available: 2,
    oversubscribed: false,
    ...over,
  };
}

function buyMore(over: Partial<BuyMore> = {}): BuyMore {
  return {
    plan_slug: "center-ops",
    purchased: 3,
    assigned: 3,
    additional_seats_required: 1,
    price_inr: "500.00",
    ...over,
  };
}

// ---------------------------------------------------------------------------
// 1. The roster IS the read's members
// ---------------------------------------------------------------------------

describe("done-when — the roster is GET /me/members, verbatim", () => {
  const rows: Member[] = [
    { email: "ceo@fracktal.in", role: "admin", status: "active" },
    { email: "intern@fracktal.in", role: "member", status: "invited" },
  ];

  it("passes the read's rows through untouched", () => {
    expect(readMembers({ members: rows })).toEqual(rows);
  });

  it("guards a malformed 2xx (a non-array `members`) rather than crashing", () => {
    expect(readMembers(null)).toEqual([]);
    expect(readMembers({} as MembersPayload)).toEqual([]);
    expect(
      readMembers({ members: "oops" as unknown as Member[] }),
    ).toEqual([]);
  });

  it("pins the array guard in the view-model, as the SC-1a panel's fix did", () => {
    expect(MANAGE).toContain("Array.isArray(payload?.members)");
  });

  it("the page renders the roster from the members read and its view-model", () => {
    expect(PAGE).toContain("/api/billing/members");
    expect(PAGE).toContain("readMembers");
  });
});

// ---------------------------------------------------------------------------
// 2. The controls POST the right bodies to the right hops
// ---------------------------------------------------------------------------

describe("done-when — the write bodies name only what R11 allows", () => {
  it("assign names member_email, plan_slug and the seat source — nothing else", () => {
    expect(assignBody("target@fracktal.in", "center-ops")).toEqual({
      member_email: "target@fracktal.in",
      plan_slug: "center-ops",
      source: ASSIGN_SOURCE,
    });
    expect(Object.keys(assignBody("a", "b")).sort()).toEqual([
      "member_email",
      "plan_slug",
      "source",
    ]);
  });

  it("release drops the source — freeing a seat needs no billing category", () => {
    expect(releaseBody("target@fracktal.in", "center-ops")).toEqual({
      member_email: "target@fracktal.in",
      plan_slug: "center-ops",
    });
    expect(Object.keys(releaseBody("a", "b")).sort()).toEqual([
      "member_email",
      "plan_slug",
    ]);
  });

  it("the source is a self-serve category, never `core`", () => {
    expect(ASSIGN_SOURCE).toBe("alacarte");
    expect(ASSIGN_SOURCE).not.toBe("core");
  });

  it("the page posts assign/release to SC-2a's two hops with those builders", () => {
    expect(PAGE).toContain("/api/billing/seats/assign");
    expect(PAGE).toContain("/api/billing/seats/release");
    expect(PAGE).toContain("assignBody");
    expect(PAGE).toContain("releaseBody");
  });
});

// ---------------------------------------------------------------------------
// 3. No seat arithmetic of its own
// ---------------------------------------------------------------------------

describe("done-when — the UI holds no seat arithmetic", () => {
  it("canAssign reads the server's clamped `available` as a boolean, not a re-derivation", () => {
    expect(canAssign(plan({ available: 2 }))).toBe(true);
    expect(canAssign(plan({ available: 0 }))).toBe(false);
    // Even an internally inconsistent row is not "fixed": the gate is the
    // server's `available`, never `purchased - assigned`.
    expect(canAssign(plan({ purchased: 2, assigned: 5, available: 9 }))).toBe(true);
  });

  it("keeps the view-model free of count arithmetic", () => {
    expect(MANAGE).not.toMatch(/Math\./);
    expect(MANAGE).not.toMatch(/available\s*[-+]/);
    expect(MANAGE).not.toMatch(/assigned\s*[-+]/);
    expect(MANAGE).not.toMatch(/purchased\s*-\s*assigned/);
    expect(MANAGE).not.toMatch(/additional_seats_required\s*[-+]/);
  });

  it("keeps the page free of count arithmetic", () => {
    expect(PAGE).not.toMatch(/Math\./);
    expect(PAGE).not.toMatch(/available\s*[-+]\s*1/);
    expect(PAGE).not.toMatch(/assigned\s*[-+]\s*1/);
    expect(PAGE).not.toMatch(/purchased\s*-\s*assigned/);
    expect(PAGE).not.toMatch(/assigned\s*-\s*purchased/);
  });
});

// ---------------------------------------------------------------------------
// 4. On success, the SC-1a read is refetched
// ---------------------------------------------------------------------------

describe("done-when — success refetches GET /me/seats, never adjusts a count", () => {
  it("threads the SC-1a loader as the change callback and awaits it on ok", () => {
    expect(PAGE).toContain("onChanged={loadSeats}");
    expect(PAGE).toContain("await onChanged()");
    // `loadSeats` is the SC-1a read, so a refetch is the counts' next value.
    expect(PAGE).toContain('/api/billing/seats"');
  });

  it("interpret returns a bare `ok` on any 2xx — the count is the read's job", () => {
    expect(interpretSeatAction(200, { assigned: true })).toEqual({ kind: "ok" });
    // Idempotent already-assigned: the Console answers 200, and so do we.
    expect(interpretSeatAction(200, { assigned: true, plan_slug: "x" })).toEqual({
      kind: "ok",
    });
    // Release no-op: `{released: false}` is still a success.
    expect(interpretSeatAction(200, { released: false })).toEqual({ kind: "ok" });
  });
});

// ---------------------------------------------------------------------------
// 5. The cap 409 renders `buy_more` verbatim
// ---------------------------------------------------------------------------

describe("done-when — the cap 409 relays buy_more verbatim, no client arithmetic", () => {
  it("carries the server's buy_more payload through untouched", () => {
    const b = buyMore();
    const body = { detail: { reason: "seat_cap_exceeded", buy_more: b } };
    expect(interpretSeatAction(409, body)).toEqual({ kind: "cap", buyMore: b });
  });

  it("builds the cap copy from the server's OWN numbers, computing none", () => {
    const msg = buyMoreMessage(buyMore());
    expect(msg).toContain("center-ops");
    expect(msg).toContain("3 of 3"); // assigned of purchased, both the read's
    expect(msg).toContain("Buy 1 more"); // additional_seats_required, the server's
    expect(msg).toContain("₹500.00"); // price_inr, verbatim
  });

  it("omits the price cleanly when the server sends none", () => {
    const msg = buyMoreMessage(buyMore({ price_inr: null }));
    expect(msg).toContain("Buy 1 more");
    expect(msg).not.toContain("₹");
  });

  it("a 409 that is NOT the cap is a generic error, never a crash", () => {
    expect(interpretSeatAction(409, { detail: "some other conflict" })).toEqual({
      kind: "error",
      message: "some other conflict",
    });
  });

  it("surfaces an upstream refusal, and a bodyless failure, as an error", () => {
    expect(interpretSeatAction(403, { detail: "not an active admin" })).toEqual({
      kind: "error",
      message: "not an active admin",
    });
    expect(interpretSeatAction(502, null).kind).toBe("error");
  });
});

// ---------------------------------------------------------------------------
// The surface gate: billing:purchase, on top of the page's admin arm
// ---------------------------------------------------------------------------

describe("done-when — the write controls are gated on billing:purchase", () => {
  it("the page hides the controls behind hasCapability(..., 'billing:purchase')", () => {
    expect(PAGE).toContain('hasCapability(access, "billing:purchase")');
    expect(PAGE).toMatch(/canManage/);
  });

  it("keeps the page's admin arm — the controls live inside it", () => {
    expect(PAGE).toContain("access?.is_admin");
  });
});
