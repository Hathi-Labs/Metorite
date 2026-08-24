/**
 * The invite→seat outcome fence (R7 · `launch_surface.md` §6.3 / LS-8).
 *
 * The claim: **a failed seat assign never un-invites anybody, and the admin can
 * always tell that the member exists.**
 *
 * LS-8's done-when names three failure paths; each has a test here asserting
 * both halves — the member still exists, and the message names the right cause.
 */

import { describe, expect, it } from "vitest";

import type { BuyMore } from "@/app/settings/billing/lib/manage";

import {
  afterInvite,
  describe as describeOutcome,
  fromInviteFailure,
  memberExists,
  shouldStayOpen,
} from "./inviteSeat";

const buyMore: BuyMore = {
  plan_slug: "core",
  purchased: 5,
  assigned: 5,
  additional_seats_required: 1,
  price_inr: "500.00",
};

describe("the happy paths", () => {
  it("reports member-and-seat when both landed", () => {
    const out = afterInvite({ kind: "ok" });
    expect(out.kind).toBe("invited-and-seated");
    expect(memberExists(out)).toBe(true);
    expect(describeOutcome(out, "Priya")).toMatch(/Priya is now a member and holds a seat/);
    // Nothing to decide — the roster behind the dialog is the confirmation.
    expect(shouldStayOpen(out)).toBe(false);
  });

  it("reports member-only when no seat was asked for", () => {
    const out = afterInvite(null);
    expect(out.kind).toBe("invited");
    expect(memberExists(out)).toBe(true);
    // Says where to go when they change their mind, rather than going silent on
    // the fact that this person cannot yet be billed for.
    expect(describeOutcome(out, "Priya")).toMatch(/Seat assignments/);
    expect(shouldStayOpen(out)).toBe(false);
  });
});

describe("path 1 — the seat cap (LS-8)", () => {
  it("keeps the member and says to buy seats, with the Console's numbers", () => {
    const out = afterInvite({ kind: "cap", buyMore });
    expect(out.kind).toBe("invited-not-seated");
    expect(memberExists(out)).toBe(true);
    if (out.kind !== "invited-not-seated") return;
    expect(out.atCap).toBe(true);

    const said = describeOutcome(out, "Priya");
    // The member exists, and that clause comes FIRST — an admin who reads only
    // the start of the message must not conclude the invite failed and re-send.
    expect(said).toMatch(/^Priya is now a member/);
    // The Console's own sentence, with the Console's own figures.
    expect(said).toMatch(/5 of 5 seats assigned/);
    expect(said).toMatch(/₹500\.00/);
    expect(said).toMatch(/unassigned/);
    // Stays open: there is a decision to make.
    expect(shouldStayOpen(out)).toBe(true);
  });
});

describe("path 2 — the seat plane is unreachable (LS-8)", () => {
  it("keeps the member and does NOT tell them to buy anything", () => {
    // The 503 an unwired deployment answers with. There is nothing to purchase
    // and nothing the admin can fix; sending them to a checkout would be wrong.
    const out = afterInvite({
      kind: "error",
      message: "Seat management is temporarily unavailable.",
    });
    expect(memberExists(out)).toBe(true);
    if (out.kind !== "invited-not-seated") return;
    expect(out.atCap).toBe(false);

    const said = describeOutcome(out, "Priya");
    expect(said).toMatch(/^Priya is now a member/);
    expect(said).toMatch(/temporarily unavailable/);
    expect(said).toMatch(/once this clears/);
    // The distinguishing assertion: no upsell on a transient failure.
    expect(said).not.toMatch(/Buy \d/);
    expect(shouldStayOpen(out)).toBe(true);
  });

  it("keeps the member on a 403 from the Console too", () => {
    // Not a seat admin. Same shape: the invite stands, the seat did not happen.
    const out = afterInvite({ kind: "error", message: "not a seat admin" });
    expect(memberExists(out)).toBe(true);
    expect(describeOutcome(out, "Priya")).toMatch(/^Priya is now a member/);
  });
});

describe("path 3 — the invite itself failed (LS-8)", () => {
  it("reports the gateway's reason and claims no member", () => {
    const out = fromInviteFailure("this address is already an active member");
    expect(out.kind).toBe("invite-failed");
    expect(memberExists(out)).toBe(false);
    // The gateway's own detail, verbatim — not wrapped in a sentence that
    // implies a member now exists.
    expect(describeOutcome(out, "Priya")).toBe(
      "this address is already an active member",
    );
    expect(shouldStayOpen(out)).toBe(true);
  });
});

describe("the message survives a missing display name", () => {
  it("falls back to a generic subject rather than an empty one", () => {
    // Display name is optional on the invite form, so this is the common case,
    // not an edge case. " is now a member" reads as a bug.
    const out = afterInvite({ kind: "ok" });
    expect(describeOutcome(out, "")).toMatch(/^The member is now a member/);
    expect(describeOutcome(out, "   ")).toMatch(/^The member/);
  });
});
