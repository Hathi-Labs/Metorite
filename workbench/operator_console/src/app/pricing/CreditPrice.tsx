"use client";

// What a credit COSTS — the saved commercial frame (migration 017, H-42).
//
// 🔴 **This panel is the one place a money assumption becomes a FACT.** The
// cockpit below explores what-ifs in component state; this panel's Save is
// an explicit POST that writes `credit_price`, and everything on the page
// seeds from what it wrote. One saved frame means two operators read the
// SAME margins — before this, each typed their own and disagreed.
//
// 🔴 **Billing never reads what this saves.** A call bills credits; the
// tier card owns how many (D67). This number converts rupees to credits
// when somebody BUYS them — today that is a bank transfer verified by an
// operator, who grants the credits on the customer's page.
//
// ⚠️ **INSERT with history, never an edit** — the tier_rate_card
// discipline. A past sale stays readable against the price that sold it.

import { useRouter } from "next/navigation";
import { useState } from "react";

import type { CreditPrice as Price } from "@/lib/contract";
import { chipClass } from "@/lib/tone";

/** "1.500000" → "1.5" — the wire is exact, the input box is for humans. */
function trim(s: string): string {
  return s.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

export default function CreditPrice({ price }: { price: Price | null }) {
  const router = useRouter();
  const [formOpen, setFormOpen] = useState(price === null);
  const [inr, setInr] = useState(trim(price?.inrPerCredit ?? ""));
  const [fx, setFx] = useState(trim(price?.usdToInr ?? ""));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const p = Number(price?.inrPerCredit);
  const example =
    price && Number.isFinite(p) && p > 0
      ? Math.floor(5000 / p).toLocaleString("en-IN")
      : null;

  async function save() {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/operator/catalog/credit-price", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          inr_per_credit: inr.trim(),
          usd_to_inr: fx.trim(),
        }),
      });
      const text = await res.text();
      setResult({
        ok: res.ok,
        text: res.ok
          ? "Saved. The margins on this page now read from it."
          : `The Console refused: ${text}`,
      });
      if (res.ok) router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>What a credit costs</h2>
        <p>
          The rupee price of <strong>one credit</strong> — the number a bank
          transfer is converted with — and the ₹/$ planning rate the margins
          below use. Billing burns credits either way; this prices the
          credits themselves. Saving is a commercial act: it needs an
          elevated admin session, and every save keeps history.
        </p>
      </div>

      {price ? (
        <p className="resultline">
          1 credit = ₹{trim(price.inrPerCredit)} · $1 = ₹{trim(price.usdToInr)}
          {price.effectiveFrom &&
            ` — since ${new Date(price.effectiveFrom).toLocaleDateString()}`}
          {example && (
            <>
              . A customer who pays ₹5,000 by transfer buys{" "}
              <strong>{example} credits</strong> — grant them on the
              customer&apos;s page.
            </>
          )}
        </p>
      ) : (
        <p className="resultline">
          <span className={chipClass("warn")}>not set</span> Until this is
          saved, the margins below run on hand-typed assumptions, and a bank
          transfer has no official credit conversion (H-42).
        </p>
      )}

      {formOpen ? (
        <div className="setup">
          <div className="formrow">
            <div className="field">
              <label htmlFor="cp-inr">One credit is ₹</label>
              <input
                id="cp-inr"
                inputMode="decimal"
                value={inr}
                onChange={(e) => setInr(e.target.value)}
                placeholder="1"
              />
            </div>
            <div className="field">
              <label htmlFor="cp-fx">One dollar is ₹</label>
              <input
                id="cp-fx"
                inputMode="decimal"
                value={fx}
                onChange={(e) => setFx(e.target.value)}
                placeholder="88"
              />
              <span className="field-hint">
                a planning rate for margins, not a live feed
              </span>
            </div>
          </div>
          <div className="job-actions">
            <button
              type="button"
              disabled={busy || !inr.trim() || !fx.trim()}
              onClick={save}
            >
              {busy ? "Saving…" : "Save the credit price"}
            </button>
            {price && (
              <button
                type="button"
                className="linklike"
                onClick={() => setFormOpen(false)}
              >
                Close
              </button>
            )}
          </div>
          {result && (
            <p className={result.ok ? "result ok" : "result err"}>
              {result.text}
            </p>
          )}
        </div>
      ) : (
        <button
          type="button"
          className="setupbtn"
          onClick={() => setFormOpen(true)}
        >
          Change the credit price
        </button>
      )}
    </section>
  );
}
