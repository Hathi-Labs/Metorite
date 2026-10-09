// Money across every customer — WS-50 slice 4, decision D94.
//
// Spec: `project-docs/specs/operator_console_money.md` §4, slice 4.
//
// 🔴 **One join, two pages.** The customer list and the Money page both show
// what each customer paid us and cost us. They read the same two Console
// responses (`/orgs` for seats and status, `/admin/usage/orgs` for credits,
// cost and draws), and they join them HERE, through `customerMoney`. So the
// list, the Money page and the customer page can never quote two profits for
// one customer.
//
// ⚠️ **A customer missing from the usage read is UNKNOWN, not zero.** The
// usage read lists every organization with zeros for a quiet one, but it is a
// capped page. A customer below the cap has no row, and this file says so
// rather than printing ₹0 of AI cost for a customer who may be the biggest
// spender we have not loaded.

import type { OrgRow } from "./format";
import { customerMoney, priceFrom, type CustomerMoney, type Price } from "./money";
import type { OrgUsageView } from "./usage";

export type FleetRow = {
  org: OrgRow;
  /** `null` when the usage read did not include this customer. */
  money: CustomerMoney | null;
};

export type FleetTotals = {
  charged: number | null;
  aiCost: number | null;
  givenAway: number | null;
  profit: number | null;
  /** A FRACTION. */
  margin: number | null;
  owed: number;
  /** Customers whose profit for the window is below zero. */
  losing: number;
  /** True when a total includes an estimate or leaves a customer out. */
  estimated: boolean;
  /** Customers with no usage row, so their money is not in the totals. */
  missing: number;
};

export type Fleet = {
  rows: FleetRow[];
  totals: FleetTotals;
  price: Price | null;
  /** False when the Console sent no credit-price field (a build before it). */
  priceReported: boolean;
  windowDays: number;
};

/** Join the roster to the usage read, one money model per customer. */
export function fleetMoney(
  orgs: OrgRow[],
  view: Partial<OrgUsageView> | null,
  now: Date,
): Fleet {
  const windowDays = view?.windowDays ?? 30;
  const price = view ? priceFrom(view.inrPerCredit, view.usdToInr) : null;
  const priceReported = !!view && "inrPerCredit" in view;
  const bySlug = new Map((view?.rows ?? []).map((r) => [r.slug, r]));

  const rows: FleetRow[] = orgs.map((org) => {
    const row = bySlug.get(org.slug);
    if (!row) return { org, money: null };
    const purchased = org.seats.reduce((n, s) => n + (s.purchased ?? 0), 0);
    return {
      org,
      money: customerMoney({
        row,
        price,
        seatsMonthlyInr: Number.isFinite(org.mrr_paise) ? org.mrr_paise / 100 : null,
        seatsBought: purchased || null,
        windowDays,
        drawsSince: view?.drawsSince ?? null,
        now,
      }),
    };
  });

  return { rows, totals: fleetTotals(rows), price, priceReported, windowDays };
}

/** Sum a figure across rows. `null` if any included row cannot answer it,
 *  and `null` when NO row has money: a failed usage read is unknown, never
 *  ₹0 (review, slice 4). */
function sum(
  rows: FleetRow[],
  pick: (m: CustomerMoney) => number | null,
): number | null {
  let total = 0;
  let counted = 0;
  for (const r of rows) {
    if (!r.money) continue;
    const v = pick(r.money);
    if (v === null) return null;
    total += v;
    counted += 1;
  }
  return counted === 0 ? null : total;
}

export function fleetTotals(rows: FleetRow[]): FleetTotals {
  const charged = sum(rows, (m) => m.charged.value);
  const aiCost = sum(rows, (m) => m.aiCost.value);
  const givenAway = sum(rows, (m) => m.givenAway.value);
  const profit = charged === null || aiCost === null ? null : charged - aiCost;
  const margin = profit === null || charged === null || charged <= 0 ? null : profit / charged;
  const owed = rows.reduce((n, r) => n + (r.money?.owed.value ?? 0), 0);
  const losing = rows.filter((r) => (r.money?.profit.value ?? 0) < 0).length;
  const missing = rows.filter((r) => r.money === null).length;
  const estimated =
    missing > 0 ||
    rows.some((r) => r.money && (r.money.profit.estimated || r.money.charged.estimated));
  return { charged, aiCost, givenAway, profit, margin, owed, losing, estimated, missing };
}

export type FleetSort = "charged" | "profit";

/** Biggest customers first, or the money-losers first. Unknown sorts last. */
export function sortFleet(rows: FleetRow[], by: FleetSort): FleetRow[] {
  const key = (r: FleetRow): number | null =>
    by === "charged" ? r.money?.charged.value ?? null : r.money?.profit.value ?? null;
  return [...rows].sort((a, b) => {
    const ka = key(a);
    const kb = key(b);
    if (ka === null && kb === null) return a.org.name.localeCompare(b.org.name);
    if (ka === null) return 1;
    if (kb === null) return -1;
    return by === "charged" ? kb - ka : ka - kb;
  });
}

/** The sentence a fleet total shows in its ⓘ. */
export function totalsHow(t: FleetTotals, fleetSize: number): string {
  const counted = fleetSize - t.missing;
  const base = `The sum over ${counted} customer${counted === 1 ? "" : "s"}.`;
  return t.missing > 0
    ? `${base} ${t.missing} customer${t.missing === 1 ? " is" : "s are"} missing from the usage read, so ${
        t.missing === 1 ? "its" : "their"
      } money is not in this total.`
    : base;
}
