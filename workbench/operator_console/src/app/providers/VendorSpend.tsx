// What each vendor COST US over the window — the other half of the money.
//
// 🔴 **Nothing in this console showed the bill.** Every other spend surface
// answers "what did a customer use": /usage groups by organization, the margin
// monitor groups by tier. Both are revenue questions. Nobody could ask "what do
// we OWE", which is the number every margin is only meaningful against — and
// the owner named it as the missing half of this section.
//
// 🔴 **Two totals, and drawing one would be the lie.** `costUsd` includes calls
// we costed ourselves by multiplying `model_profile` prices against tokens we
// counted, and that is only as fresh as the last edit to that table.
// `measuredUsd` is only the calls where the vendor STATED the charge
// (migration 031), so it is the figure an invoice reconciles against. One
// blended number would hide which half a reader was acting on.
//
// ⚠️ **A SERVER component.** Every judgement is pure and the read happens on
// the page with the caller's own token. An operator-wide money figure belongs
// on no path the browser can replay.
//
// ⚠️ **Empty is a real state and says so.** No traffic in the window is the
// shipped state, and a table of zeros would look like a fault.

import type { ProviderSpend } from "@/lib/contract";
import { providerGlyph } from "@/lib/categorical";
import { chipClass } from "@/lib/tone";

/** USD with enough places to be checkable, and grouped so it is readable.
 *
 * ⚠️ **Parsed here and nowhere earlier.** The value crosses the wire as a
 * string because the column is NUMERIC, and this is the last step before it is
 * drawn. Four places, because a month of cheap calls is real money at the
 * fourth and rounding to two would show a busy vendor as costing nothing. */
function usd(value: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `$${n.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })}`;
}

function sum(rows: ProviderSpend[], key: "costUsd" | "measuredUsd"): number {
  return rows.reduce((t, r) => {
    const n = Number(r[key]);
    return t + (Number.isFinite(n) ? n : 0);
  }, 0);
}

export default function VendorSpend({
  spend,
  days,
}: {
  spend: ProviderSpend[];
  days: number;
}) {
  const total = sum(spend, "costUsd");
  const measured = sum(spend, "measuredUsd");
  // ⚠️ Guarded against a zero total, which is the empty state — not a bug, and
  // not something to divide by.
  const fullyMeasured = total > 0 && measured >= total;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>What the vendors cost us</h2>
        <p>
          Our own bill over the last {days} days, by vendor — the number every
          margin is measured against. A customer&apos;s own key (BYOK) never
          appears here: those tokens run on their account, not ours.
        </p>
      </div>

      {spend.length === 0 ? (
        <p className="field-hint">
          No calls we paid for in this window. That is the shipped state until
          a tier serves its first request.
        </p>
      ) : (
        <>
          <div className="banner ok" role="status">
            <strong>{usd(String(total))}</strong> across{" "}
            {spend.length === 1 ? "one vendor" : `${spend.length} vendors`}.{" "}
            {fullyMeasured ? (
              <>
                Every dollar of it was stated by the vendor, so it reconciles
                against an invoice directly.
              </>
            ) : (
              <>
                <strong>{usd(String(measured))}</strong> of that was stated by
                the vendor. The rest we costed ourselves from{" "}
                <code>model_profile</code>, so it is an estimate and only as
                fresh as the last time somebody edited those prices.
              </>
            )}
          </div>

          <table className="grid">
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Calls</th>
                <th>Cost</th>
                {/* 🔴 The reconcilable part, beside the total rather than
                    blended into it. */}
                <th>Vendor stated</th>
              </tr>
            </thead>
            <tbody>
              {spend.map((r) => {
                const rowTotal = Number(r.costUsd);
                const rowMeasured = Number(r.measuredUsd);
                const allMeasured =
                  Number.isFinite(rowTotal) &&
                  Number.isFinite(rowMeasured) &&
                  rowTotal > 0 &&
                  rowMeasured >= rowTotal;
                return (
                  <tr key={r.provider}>
                    <td className="mono">
                      <span aria-hidden="true">{providerGlyph(r.provider)}</span>{" "}
                      {r.provider}
                    </td>
                    <td className="mono">{r.calls}</td>
                    <td className="mono">{usd(r.costUsd)}</td>
                    <td>
                      {rowMeasured > 0 ? (
                        <span className={chipClass(allMeasured ? "ok" : "warn")}>
                          {usd(r.measuredUsd)}
                        </span>
                      ) : (
                        // ⚠️ A dash, not a zero. This vendor stated nothing, so
                        // there is no measured figure — which is different from
                        // a measured figure that happens to be zero.
                        <span className="muted" title="This vendor reports no cost, so the figure beside it is our own arithmetic.">
                          —
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <p className="note">
            A vendor that reports its own cost needs no price kept in{" "}
            <a href="/models">Models</a> — it states the charge on every call.
            One that reports nothing is costed from the prices recorded there,
            so those have to stay current for its margin to mean anything.
          </p>
        </>
      )}
    </section>
  );
}
