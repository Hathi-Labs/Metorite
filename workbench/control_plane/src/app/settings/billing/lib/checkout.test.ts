/**
 * The checkout's decisions, and the two source-level rules behind them.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a launch done-whens
 * 1, 5a, 6 and 9 · SC-4g done-when 4's refusal partition.
 *
 * Three parts, and the two source scans are not decoration:
 *
 *   1. **done-when 1** — no price is transcribed into TypeScript. A price
 *      change is a database row (D23/D24's own rule), so a surface carrying a
 *      figure is a second source of truth for money. Nothing behavioural can
 *      see this: a hard-coded ladder renders perfectly.
 *   2. **done-when 6** — the whole purchase flow is mounted only inside the
 *      `data.purchaseEnabled` conditional. "It ships dark" is exactly the kind
 *      of claim that quietly stops being true, and R7 says a rule with no test
 *      is advisory.
 *   3. The behaviour: terminal-order arithmetic (5a) and the refusal partition
 *      (9), both pure.
 *
 * ⚠️ Source-regex is the right tool for 1 and 6 and the WRONG tool for B7's
 * gate. Here the subject is *where a thing may appear in a file*, which is
 * exactly what a source assertion decides. B7's subject is *whether a refusal
 * happens before a network call*, which only running the handler can decide —
 * see `src/app/api/billing/checkout.test.ts`.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  type Order,
  basketLines,
  formatPaise,
  orderState,
  redeemRefusal,
  terminalCopy,
} from "./checkout";

function source(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf-8");
}

/**
 * The file with its comments removed.
 *
 * Every scan below is about what the surface **renders** or **carries**, and a
 * comment does neither: these files document the ladder and the flip point at
 * length, and a scan that counted prose would fail on its own explanation —
 * which is the kind of fence people delete rather than fix.
 */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const PAGE = code(source("../page.tsx"));
const CHECKOUT = code(source("../Checkout.tsx"));
const MODULE = code(source("./checkout.ts"));

// ---------------------------------------------------------------------------
// 1. done-when 1 — never a hard-coded ladder in TypeScript
// ---------------------------------------------------------------------------

/**
 * The D23/D24 ladder, in BOTH denominations.
 *
 * Rupees are what the decision records (`core` ₹600 · app-bearing Centers ₹600
 * · slices-only Centers ₹300 · `builder` ₹500 · `workflows` ₹300 ·
 * `all_centers` ₹1,800 · `complete` ₹3,000) and paise are what the wire
 * carries, so a transcription could arrive in either. Both are forbidden.
 */
const LADDER_FIGURES = [
  300, 500, 600, 1800, 3000, 30000, 50000, 60000, 180000, 300000,
];

describe("done-when 1 — the surface transcribes no price", () => {
  it.each([
    ["page.tsx", PAGE],
    ["Checkout.tsx", CHECKOUT],
    ["lib/checkout.ts", MODULE],
  ])("%s carries no figure from the D23/D24 ladder", (_name, text) => {
    for (const figure of LADDER_FIGURES) {
      expect(
        new RegExp(`(?<![\\w.-])${figure}(?![\\w.])`).test(text),
        `${figure} appears as a literal — done-when 1 forbids transcribing a price`,
      ).toBe(false);
    }
  });

  it("Checkout.tsx renders no currency symbol of its own", () => {
    // Money is formatted in one place (`formatPaise` → `formatRupees`), so a
    // rupee sign written into the markup would mean a second formatter — and
    // a second formatter is where a figure gets typed next to it.
    expect(CHECKOUT).not.toMatch(/₹\s*\d/);
  });

  it("the ladder comes from the catalog route, not from a constant", () => {
    expect(CHECKOUT).toContain("/api/billing/catalog");
    // A slug list in the client would be the same defect in a different
    // denomination: it decides WHAT is sellable, which is `WHERE active`'s job.
    expect(MODULE).not.toContain("all_centers");
    expect(CHECKOUT).not.toContain("all_centers");
  });
});

// ---------------------------------------------------------------------------
// 2. done-when 6 — the flow lives only in the `purchaseEnabled` arm
// ---------------------------------------------------------------------------

describe("done-when 6 — the checkout ships dark behind purchaseEnabled", () => {
  it("mounts <Checkout /> exactly once, and inside the true arm", () => {
    const mounts = PAGE.match(/<Checkout\b/g) ?? [];
    expect(mounts).toHaveLength(1);

    const flag = PAGE.indexOf("data.purchaseEnabled ? (");
    const mount = PAGE.indexOf("<Checkout");
    const falseArm = PAGE.indexOf("To add credits, contact us");

    expect(flag).toBeGreaterThan(-1);
    // Strictly between the conditional and the contact prompt: that span IS
    // the true arm, and an occurrence outside it is a control rendered to
    // somebody the owner has not flipped the flag for.
    expect(mount).toBeGreaterThan(flag);
    expect(mount).toBeLessThan(falseArm);
  });

  it("keeps the purchase flow itself out of page.tsx entirely", () => {
    // The entry control, the package picker and the code entry all live in
    // `Checkout.tsx`, which has exactly one mount point (above). Anything of
    // theirs appearing in the page would be reachable without the flag.
    for (const marker of [
      "/api/billing/orders",
      "/api/billing/catalog",
      "redeem",
      "PackagePicker",
      "CodeEntry",
    ]) {
      expect(PAGE, `${marker} must not appear outside the checkout`).not.toContain(
        marker,
      );
    }
  });

  it("leaves the false arm as the contact prompt", () => {
    expect(PAGE).toContain("To add credits, contact us");
  });
});

// ---------------------------------------------------------------------------
// 3. done-when 5a — terminal orders, and the clock
// ---------------------------------------------------------------------------

const NOW = Date.parse("2026-08-19T12:00:00Z");

function order(overrides: Partial<Order> = {}): Order {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    status: "created",
    provider: "razorpay",
    gross_paise: 1,
    discount_paise: 0,
    taxable_paise: 1,
    gst_paise: 0,
    total_paise: 1,
    gst_split: "cgst_sgst",
    expires_at: "2026-08-19T12:30:00Z",
    created_at: "2026-08-19T11:30:00Z",
    terminal_at: null,
    lines: [],
    discount: null,
    ...overrides,
  };
}

describe("done-when 5a — a terminal order is never re-used", () => {
  it("treats captured and abandoned as terminal, and nothing else", () => {
    expect(orderState(order({ status: "captured" }), NOW).terminal).toBe(true);
    expect(orderState(order({ status: "abandoned" }), NOW).terminal).toBe(true);
    // `attempted` is on the graph and unwritten; `failed` has NO writer at all
    // (CP-9's review P0), so an open order stays payable after a failed
    // attempt. A page that branched on a third terminal state would be
    // branching on a state that cannot occur.
    expect(orderState(order({ status: "created" }), NOW).terminal).toBe(false);
    expect(orderState(order({ status: "attempted" }), NOW).terminal).toBe(false);
  });

  it("treats a past expires_at as terminal even while status reads open", () => {
    const stale = order({ expires_at: "2026-08-19T11:59:00Z" });
    const state = orderState(stale, NOW);
    expect(state.terminal).toBe(true);
    expect(state.reason).toBe("expired");
  });

  it("does not guess terminal from an unparseable expiry", () => {
    // The TTL is enforced server-side by `abandon_if_expired` on every touch,
    // so a date format the browser cannot read must not take a working
    // checkout down. The refusal would be a 409 either way.
    expect(orderState(order({ expires_at: "not a date" }), NOW).terminal).toBe(
      false,
    );
  });

  it("reads a naive timestamp as UTC rather than as local time", () => {
    // A machine an hour behind UTC would otherwise treat a live order as
    // expired — or, worse, a dead one as live.
    expect(
      orderState(order({ expires_at: "2026-08-19T11:59:00" }), NOW).terminal,
    ).toBe(true);
  });

  it("says a discount moved the amount, so earlier figures are stale", () => {
    const redeemed = order({
      status: "captured",
      total_paise: 0,
      discount: { code_prefix: "abc", discount_paise: 1 },
    });
    const state = orderState(redeemed, NOW);
    expect(state.discounted).toBe(true);
    expect(state.completed).toBe(true);
  });

  it("does not call a captured order with money on it complete", () => {
    // The ₹0 journey's end is captured AND nothing left to pay. A captured
    // order with a balance is SC-4h's world, not this slice's.
    expect(
      orderState(order({ status: "captured", total_paise: 1 }), NOW).completed,
    ).toBe(false);
  });

  it("names every terminal reason", () => {
    for (const reason of ["captured", "abandoned", "expired"] as const) {
      expect(terminalCopy(reason).length).toBeGreaterThan(0);
    }
  });
});

// ---------------------------------------------------------------------------
// 4. done-when 9 — the refusals partition
// ---------------------------------------------------------------------------

describe("done-when 9 / SC-4g done-when 4 — the refusals partition", () => {
  const reasons = ["expired", "revoked", "exhausted"] as const;

  it("gives the three named reasons THREE distinct messages", () => {
    const messages = reasons.map(
      (reason) => redeemRefusal(409, { detail: { reason } }).message,
    );
    expect(new Set(messages).size).toBe(3);
    for (const [i, reason] of reasons.entries()) {
      expect(redeemRefusal(409, { detail: { reason } }).kind).toBe(reason);
      expect(messages[i].length).toBeGreaterThan(0);
    }
  });

  it("gives unknown and wrong-org ONE identical message", () => {
    // Upstream they are byte-identical on purpose — a distinguishable
    // "wrong org" confirms a code exists and belongs to somebody, which is a
    // membership test over other tenants' data run from a customer's key. A
    // surface that told them apart would defeat the partition in the browser.
    const unknown = redeemRefusal(404, { detail: "no such discount code" });
    const wrongOrg = redeemRefusal(404, { detail: "no such discount code" });
    expect(unknown).toEqual(wrongOrg);
    expect(unknown.kind).toBe("no_such_code");
    // And it must not be reachable from any of the three named reasons.
    for (const reason of reasons) {
      expect(redeemRefusal(409, { detail: { reason } }).message).not.toBe(
        unknown.message,
      );
    }
  });

  it("tells the two ORDER-level 409s apart from the code ones", () => {
    const stacked = redeemRefusal(409, {
      detail: { reason: "already_discounted" },
    });
    const closed = redeemRefusal(409, {
      detail: { reason: "order_not_open", status: "abandoned" },
    });
    expect(stacked.kind).toBe("already_discounted");
    expect(closed.kind).toBe("order_not_open");
    // The order's own status reaches the copy: "this order is abandoned" is
    // actionable where "bad code" is a lie about which object failed.
    expect(closed.message).toContain("abandoned");
    expect(stacked.message).not.toBe(closed.message);
  });

  it("tells a missing ORDER apart from a missing CODE, on the server's own wording", () => {
    const noOrder = redeemRefusal(404, { detail: "no such order" });
    expect(noOrder.kind).toBe("no_such_order");
    expect(noOrder.message).not.toBe(
      redeemRefusal(404, { detail: "no such discount code" }).message,
    );
  });

  it("carries the BFF's own 401 and 403 through as themselves", () => {
    expect(redeemRefusal(401, null).kind).toBe("unauthenticated");
    expect(redeemRefusal(403, { detail: "nope" }).kind).toBe("forbidden");
    expect(redeemRefusal(403, { detail: "nope" }).message).toBe("nope");
  });

  it("never renders a raw status code at somebody", () => {
    const odd = redeemRefusal(500, null);
    expect(odd.message).not.toMatch(/\b500\b/);
    expect(odd.message.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// 5. The basket, and the one conversion
// ---------------------------------------------------------------------------

describe("the basket", () => {
  const plans = [
    { slug: "a", name: "A", kind: "center", price_paise: 1, sort_order: 10 },
    { slug: "b", name: "B", kind: "addon", price_paise: 2, sort_order: 20 },
  ];

  it("carries slugs and quantities and nothing else", () => {
    expect(basketLines(plans, { a: 2, b: 1 })).toEqual([
      { plan_slug: "a", quantity: 2 },
      { plan_slug: "b", quantity: 1 },
    ]);
  });

  it("drops anything not chosen", () => {
    expect(basketLines(plans, { a: 0, b: 3 })).toEqual([
      { plan_slug: "b", quantity: 3 },
    ]);
    expect(basketLines(plans, {})).toEqual([]);
  });

  it("orders by the catalog rather than by object-key iteration", () => {
    // The Console prices it either way; a request whose line order depends on
    // insertion order is a request nobody can diff.
    expect(basketLines(plans, { b: 1, a: 1 }).map((l) => l.plan_slug)).toEqual([
      "a",
      "b",
    ]);
  });

  it("formats paise as rupees, and that is the only conversion", () => {
    expect(formatPaise(123456)).toContain("1,234.56");
    expect(formatPaise(0)).toContain("0");
  });
});
