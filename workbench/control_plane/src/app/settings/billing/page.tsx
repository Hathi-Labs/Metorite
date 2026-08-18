"use client";

/**
 * Settings → Billing — the customer admin's credit monitor and bill archive.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-1b · SC-4 · SC-5 ·
 * decisions D37 (the credit product) and D38 (billing documents are ours).
 *
 * This is the *customer's* billing surface: one organization, admin-gated. The
 * operator's cross-organization view is a **separate deployable app** (D35) and
 * must never become a route in here — they share tables and never routes.
 *
 * Every figure is served by the Control Plane and rendered here. Nothing on
 * this page recomputes a balance: `SUM(credit_ledger)` has exactly one
 * implementation (WS-31 §3.4), and a second one in the browser is how two
 * surfaces come to disagree about a customer's money.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import Icon from "@/components/Icon";
import { useAccess } from "@/components/AccessProvider";
import Button from "@/components/ui/Button";

import Checkout from "./Checkout";
import {
  type CreditSummary,
  type InvoiceRow,
  balanceTone,
  creditsToRupees,
  formatCredits,
  formatRupees,
  invoiceKindLabel,
  runway,
  sortInvoices,
} from "./lib/billing";

interface BillingPayload {
  credits: CreditSummary;
  invoices: InvoiceRow[];
  /** Absent until CP-8/SC-4a ships checkout; the page must not assume it. */
  purchaseEnabled: boolean;
}

const TONE_CLASS = {
  ok: "text-success",
  warning: "text-warning",
  danger: "text-destructive",
} as const;

const TONE_WRAP = {
  ok: "border-border",
  warning: "border-warning/40 bg-warning/10",
  danger: "border-destructive/40 bg-destructive/10",
} as const;

const STATUS_CLASS: Record<InvoiceRow["status"], string> = {
  paid: "text-success",
  due: "text-warning",
  failed: "text-destructive",
  credit_note: "text-muted-foreground",
};

const STATUS_LABEL: Record<InvoiceRow["status"], string> = {
  paid: "Paid",
  due: "Due",
  failed: "Failed",
  credit_note: "Credit note",
};

export default function BillingPage() {
  const { access } = useAccess();
  const [data, setData] = useState<BillingPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/billing/summary", { cache: "no-store" });
      setError("");
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail ?? `Could not load billing (${r.status})`);
      }
      setData(await r.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load billing.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Wrapped rather than called directly: the effect must not reach a
    // setState synchronously, and `load` is also used by the retry button.
    const run = async () => {
      await load();
    };
    void run();
  }, [load]);

  if (!access?.is_admin) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-muted-foreground">Billing is admin-only.</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-4 py-3 shrink-0 sm:px-6 sm:py-4">
        <div className="flex items-center gap-3">
          <Link
            href="/settings"
            className="rounded-lg border border-border p-2 text-muted-foreground tech-transition hover:bg-secondary"
            aria-label="Back to settings"
          >
            <Icon name="ArrowLeft" size={15} />
          </Link>
          <div>
            <h1 className="text-base font-bold text-foreground sm:text-lg">Billing</h1>
            <p className="mt-0.5 text-xs text-muted-foreground">
              AI credits, subscription and bills
            </p>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4 sm:p-6">
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : error ? (
          <div className="flex flex-col items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/10 p-4">
            <p className="text-sm text-destructive">{error}</p>
            <Button variant="secondary" size="sm" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        ) : data ? (
          <div className="flex flex-col gap-6">
            <CreditPanel data={data} />
            <InvoiceTable rows={data.invoices} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

function CreditPanel({ data }: { data: BillingPayload }) {
  const { credits } = data;
  const tone = balanceTone(credits);
  const left = runway(credits);

  return (
    <section className={`rounded-xl border p-4 sm:p-5 ${TONE_WRAP[tone]}`}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs text-muted-foreground">AI credit balance</p>
          <p className={`mt-1 text-2xl font-bold ${TONE_CLASS[tone]}`}>
            {formatCredits(credits.balanceCredits)}
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              ≈ {formatRupees(creditsToRupees(credits.balanceCredits))}
            </span>
          </p>

          {/* SC-4c: the figure that actually prompts a purchase. A balance
              alone does not. "Unknown" is printed as such rather than as a
              reassuring large number. */}
          <p className="mt-1 text-xs text-muted-foreground">
            {left.kind === "days"
              ? `About ${left.days} ${left.days === 1 ? "day" : "days"} left at your recent rate`
              : left.kind === "empty"
                ? "Out of credits — top up to resume AI features"
                : left.reason === "no usage yet"
                  ? "No usage yet — we cannot estimate how long this will last"
                  : "More than 90 days at your recent rate"}
          </p>
        </div>

        <div className="text-right">
          <p className="text-xs text-muted-foreground">
            Used in the last {credits.windowDays} days
          </p>
          <p className="mt-1 text-lg font-semibold text-foreground">
            {formatCredits(credits.burnThisCycle)}
          </p>
        </div>
      </div>

      {/* §3.4 — a BYOK organization is metered but not charged for tokens, and
          must never be shown a figure that reads like a bill. */}
      {credits.isByok ? (
        <p className="mt-4 rounded-lg bg-secondary p-3 text-xs text-muted-foreground">
          Your organization uses its own provider key. Usage above is shown for
          visibility and is <span className="font-medium">not billed</span>.
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {/* SC-4a done-when 6 — THE flip point. `purchaseEnabled` comes from
            `GET /me/billing` (`customer_console/main.py:1408`) and flipping it
            is the OWNER's act, so the whole checkout ships dark behind this
            one conditional. The entire purchase flow lives inside `<Checkout>`
            and `<Checkout>` is mounted here and nowhere else — which is what
            `lib/checkout.test.ts`'s source fence over this file asserts, since
            "it ships dark" is exactly the kind of claim that quietly stops
            being true. */}
        {data.purchaseEnabled ? (
          <Checkout />
        ) : (
          // Honest placeholder rather than a dead button. Self-serve checkout
          // is SC-4a and is deliberately sequenced after metering (D37.1);
          // until then top-ups are arranged with us (D19.4's manage-only
          // launch posture).
          <p className="text-xs text-muted-foreground">
            To add credits, contact us — self-serve top-up is coming.
          </p>
        )}
      </div>
    </section>
  );
}

function InvoiceTable({ rows }: { rows: InvoiceRow[] }) {
  const sorted = sortInvoices(rows);

  return (
    <section>
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-foreground">Bills</h2>
        <p className="text-xs text-muted-foreground">
          Subscription and AI credits, newest first
        </p>
      </div>

      {sorted.length === 0 ? (
        <p className="rounded-xl border border-border p-4 text-sm text-muted-foreground">
          No bills yet. They appear here as soon as the first one is issued.
        </p>
      ) : (
        // Wide content scrolls inside its own container — the page body must
        // never scroll horizontally.
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="border-b border-border bg-secondary">
              <tr className="text-xs text-muted-foreground">
                <th className="px-4 py-2 font-medium">Invoice</th>
                <th className="px-4 py-2 font-medium">Type</th>
                <th className="px-4 py-2 font-medium">Period</th>
                <th className="px-4 py-2 font-medium">Issued</th>
                <th className="px-4 py-2 text-right font-medium">Amount</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <tr key={row.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-2 font-mono text-xs text-foreground">
                    {row.serial}
                  </td>
                  <td className="px-4 py-2 text-foreground">
                    {invoiceKindLabel(row.kind)}
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {row.periodLabel ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">{row.issuedOn}</td>
                  <td className="px-4 py-2 text-right text-foreground">
                    {formatRupees(row.amountInr)}
                  </td>
                  <td className={`px-4 py-2 ${STATUS_CLASS[row.status]}`}>
                    {STATUS_LABEL[row.status]}
                  </td>
                  <td className="px-4 py-2 text-right">
                    {row.downloadUrl ? (
                      // A real navigation, not a script-driven save: the bill
                      // is served by our own route so it keeps working without
                      // a processor round-trip (D38.4).
                      <a
                        href={row.downloadUrl}
                        className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-muted-foreground tech-transition hover:bg-secondary"
                      >
                        <Icon name="Download" size={13} />
                        PDF
                      </a>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
