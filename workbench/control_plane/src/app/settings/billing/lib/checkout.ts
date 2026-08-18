/**
 * The checkout's decisions, framework-free.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a launch done-whens
 * 1, 2, 5a and 9 · SC-4g done-when 4's refusal partition ·
 * `customer_console.md` §6 CP-9 §9.2 / §9.3 / §9.3a for the wire shapes.
 *
 * Same reason `billing.ts` is a separate module: every value here is money or a
 * statement about money, and both deserve tests that do not need a browser.
 * `Checkout.tsx` renders; this decides.
 *
 * ## Three rules that are the whole point of the file
 *
 * **1. The browser formats; it never arithmetics** (§9.2). Prices arrive as
 * integer paise and are divided by 100 exactly once, at the moment of display,
 * by {@link formatPaise}. There is **no basket total in this module** — none is
 * computed anywhere on the client. The order's `gross_paise`, `gst_paise` and
 * `total_paise` come back from `POST /billing/orders`, priced by the Console
 * from `plan_catalog`. A client-side sum would be a second answer to "what does
 * this cost", and the one that disagrees in the customer's favour is the one
 * that costs money.
 *
 * **2. Nothing here knows a price.** Done-when 1 forbids a hard-coded ladder in
 * TypeScript, so the plans are whatever `GET /billing/catalog` returned and
 * this module never names a slug, a rupee figure or a paise figure. Fenced by
 * `checkout.test.ts`'s source scan over this file *and* the component.
 *
 * **3. A terminal order is never re-used** (done-when 5a). `captured` and
 * `abandoned` are the only terminal statuses — nothing writes `failed` at order
 * level (CP-9's review P0) — and an order past `expires_at` is treated as
 * terminal even while its `status` still reads open, because `abandon_if_expired`
 * is written by the clock on the next touch and the page is looking at a status
 * the server is about to change.
 */

import { formatRupees } from "./billing";

// ── The wire shapes (CP-9 §9.3a, §6 item (f)) ───────────────────────────────

/** One sellable catalog row. Five fields, and the money one is paise. */
export interface CatalogPlan {
  slug: string;
  name: string;
  kind: string;
  price_paise: number;
  sort_order: number;
}

export interface OrderLine {
  plan_slug: string;
  quantity: number;
  unit_price_paise: number;
}

/** Never the code's secret — only the clear prefix (SC-4g (i)). */
export interface OrderDiscount {
  code_prefix: string;
  discount_paise: number;
}

export interface Order {
  id: string;
  status: string;
  provider: string;
  gross_paise: number;
  discount_paise: number;
  taxable_paise: number;
  gst_paise: number;
  total_paise: number;
  gst_split: string | null;
  expires_at: string;
  created_at: string;
  terminal_at: string | null;
  lines?: OrderLine[] | null;
  discount?: OrderDiscount | null;
}

// ── Money, one direction only ───────────────────────────────────────────────

/**
 * Paise on the wire → rupees on the screen, and that is the only conversion.
 *
 * Not "arithmetic" in §9.2's sense: it is the render step, done once, on a
 * figure the server already decided. Every other number the page shows is the
 * server's own field, printed.
 */
export function formatPaise(paise: number): string {
  return formatRupees(paise / 100);
}

// ── The basket (done-when 2) ────────────────────────────────────────────────

/** slug → quantity. Absent or non-positive means "not in the basket". */
export type Selection = Readonly<Record<string, number>>;

export interface BasketLine {
  plan_slug: string;
  quantity: number;
}

/**
 * The request body for `POST /billing/orders` — slugs and quantities, no
 * amount, no currency and no code.
 *
 * Ordered by the catalog's own `sort_order` so the same basket always produces
 * the same body; the Console prices it either way, but a request whose line
 * order depends on object-key iteration is a request nobody can diff.
 */
export function basketLines(
  plans: readonly CatalogPlan[],
  selection: Selection,
): BasketLine[] {
  return plans
    .filter((plan) => (selection[plan.slug] ?? 0) > 0)
    .map((plan) => ({ plan_slug: plan.slug, quantity: selection[plan.slug] }));
}

// ── Order state (done-when 5a) ──────────────────────────────────────────────

export type TerminalReason = "captured" | "abandoned" | "expired";

export interface OrderState {
  /** No redeem, no hand-off, no retry — start a new order. */
  terminal: boolean;
  reason: TerminalReason | null;
  /** True once a code has moved the amount; every earlier figure is stale. */
  discounted: boolean;
  /** The ₹0 completion done-when 9 describes: captured, nothing left to pay. */
  completed: boolean;
}

/**
 * ISO-8601 → epoch ms, or `null` when it will not parse.
 *
 * The Console's `expires_at` is `TIMESTAMPTZ` and arrives with an offset, so
 * the fallback is defensive rather than expected: a naive string is read as
 * UTC, because the server is. **An unparseable value is not treated as
 * expired** — the TTL is enforced server-side by `abandon_if_expired` on every
 * touch and a refusal is a 409, so guessing "terminal" here would take a
 * working checkout down over a date format.
 */
function instant(value: string | null | undefined): number | null {
  if (!value) return null;
  const withZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
  const ms = Date.parse(withZone);
  return Number.isNaN(ms) ? null : ms;
}

/** The only two terminal statuses. Nothing writes `failed` (CP-9 review P0). */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set(["captured", "abandoned"]);

export function orderState(order: Order, nowMs: number): OrderState {
  const expiry = instant(order.expires_at);
  const pastExpiry = expiry !== null && expiry <= nowMs;
  const reason: TerminalReason | null =
    order.status === "captured"
      ? "captured"
      : order.status === "abandoned"
        ? "abandoned"
        : pastExpiry
          ? "expired"
          : null;

  return {
    terminal: TERMINAL_STATUSES.has(order.status) || pastExpiry,
    reason,
    discounted: !!order.discount,
    completed: order.status === "captured" && order.total_paise === 0,
  };
}

/** What the page says when an order can no longer be acted on (5a). */
export function terminalCopy(reason: TerminalReason): string {
  switch (reason) {
    case "captured":
      return "This order is already complete.";
    case "abandoned":
      return "This order was left open too long and has been closed. Start a new one.";
    case "expired":
      return "This order has expired. Start a new one.";
  }
}

// ── Refusals (SC-4g done-when 4's partition, SC-4a done-when 9) ─────────────

export type RefusalKind =
  /** The three reasons this organization is entitled to be told apart. */
  | "expired"
  | "revoked"
  | "exhausted"
  /** {unknown, wrong-org}, collapsed upstream into ONE shape. */
  | "no_such_code"
  /** Order-level 409s — statements about the order, not about the code. */
  | "already_discounted"
  | "order_not_open"
  /** The order itself is gone, or was never this organization's. */
  | "no_such_order"
  | "forbidden"
  | "unauthenticated"
  | "unavailable";

export interface Refusal {
  kind: RefusalKind;
  message: string;
}

/**
 * The collapsed shape's copy, defined ONCE.
 *
 * {unknown, wrong-org} are byte-identical upstream on purpose: a
 * distinguishable "wrong org" confirms that a code exists and belongs to
 * somebody, which is a membership test over other tenants' data run from a
 * customer's own key (CP-3's non-oracle rule). A surface that told them apart
 * would defeat, in the browser, a partition the server was built to hold — so
 * there is one constant and one call site.
 */
const NO_SUCH_CODE = "We do not recognise that code. Check it and try again.";

/** The three distinct reasons. Named here so the partition is readable. */
const CODE_REASONS: Record<string, string> = {
  expired: "That code has expired.",
  revoked: "That code has been withdrawn and can no longer be used.",
  exhausted: "That code has already been used the maximum number of times.",
};

const ORDER_REASONS: Record<string, string> = {
  already_discounted:
    "This order already has a different code applied. Codes cannot be combined — start a new order to use another one.",
  order_not_open:
    "This order is closed and cannot be changed. Start a new one.",
};

/**
 * Turn the Console's refusal into copy, preserving the partition.
 *
 * `body` is the relayed upstream JSON (the write proxies pass 400/404/409
 * through verbatim, `api/billing/_console.ts`). The 404s are told apart by the
 * Console's **own** `detail` string rather than by anything invented here.
 */
export function redeemRefusal(status: number, body: unknown): Refusal {
  const detail = (body as { detail?: unknown } | null)?.detail;

  if (status === 401) {
    return { kind: "unauthenticated", message: "Sign in to continue." };
  }
  if (status === 403) {
    return {
      kind: "forbidden",
      message:
        typeof detail === "string"
          ? detail
          : "You do not have permission to make purchases for this organization.",
    };
  }

  if (status === 404) {
    // Two different objects, two different sentences — and the Console's own
    // wording is the discriminator, so this hop invents no vocabulary.
    if (typeof detail === "string" && detail.includes("order")) {
      return {
        kind: "no_such_order",
        message: "That order is no longer available. Start a new one.",
      };
    }
    return { kind: "no_such_code", message: NO_SUCH_CODE };
  }

  if (status === 409 && detail && typeof detail === "object") {
    const reason = (detail as { reason?: unknown }).reason;
    if (typeof reason === "string") {
      if (reason in CODE_REASONS) {
        return {
          kind: reason as RefusalKind,
          message: CODE_REASONS[reason],
        };
      }
      if (reason in ORDER_REASONS) {
        const status_ = (detail as { status?: unknown }).status;
        return {
          kind: reason as RefusalKind,
          message:
            reason === "order_not_open" && typeof status_ === "string"
              ? `This order is ${status_} and cannot be changed. Start a new one.`
              : ORDER_REASONS[reason],
        };
      }
    }
  }

  if (status === 400 && typeof detail === "string") {
    return { kind: "unavailable", message: detail };
  }

  return {
    kind: "unavailable",
    message:
      typeof detail === "string"
        ? detail
        : "We could not apply that code just now. Try again in a moment.",
  };
}
