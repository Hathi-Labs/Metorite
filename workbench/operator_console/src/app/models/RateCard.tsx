"use client";

// What we charge — the rate card.
//
// 🔴 **Every card ships `unpriced`, and that is a decision waiting on a person,
// not a bug.** Setting a real number is the owner's commercial act (H-42). An
// operator reading this must not think the zeros are an oversight they should
// quietly fix, so the page says it in words above the table.
//
// ⚠️ **Not the vendor's price.** The Models list above shows what the VENDOR
// charges us, per million tokens. This shows what a CUSTOMER is billed, in the
// unit that task is measured in. Reading one as the other inverts a margin,
// which is why they are never drawn in the same table.
//
// ⚠️ **Money renders as the STRING the Console sent.** The ledger is
// NUMERIC(14,4), and re-formatting a parsed float is how a total stops matching
// the sum of its rows.

import { useState } from "react";

import { describeRate } from "@/lib/catalog";
import type { ModelRate, Task } from "@/lib/contract";
import { chipClass, pricingTone } from "@/lib/tone";

export default function RateCard({
  rates,
  tasks,
}: {
  rates: ModelRate[];
  tasks: Task[];
}) {
  const [open, setOpen] = useState(false);
  const unpriced = rates.filter((r) => r.mode === "unpriced").length;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>What we charge</h2>
        <p>
          This is the customer&apos;s price, in the unit that job is measured
          in — not what the vendor charges us. Setting a real number is a
          commercial decision.
        </p>
      </div>

      {rates.length === 0 ? (
        <p className="muted">
          No prices are set. Anything that runs will bill nothing.
        </p>
      ) : (
        <>
          <p className="resultline">
            {unpriced === 0
              ? `${rates.length} priced.`
              : `${unpriced} of ${rates.length} have no price. They will answer customers and charge nothing.`}
          </p>

          {open && (
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Job</th>
                    <th>State</th>
                    <th>Charged</th>
                  </tr>
                </thead>
                <tbody>
                  {rates.map((r) => (
                    <tr key={`${r.model}/${r.task}`}>
                      <td className="mono">{r.model}</td>
                      <td>{tasks.find((t) => t.slug === r.task)?.label ?? r.task}</td>
                      <td>
                        <span className={chipClass(pricingTone(r.mode))}>{r.mode}</span>
                      </td>
                      <td>{describeRate(r)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <button type="button" className="linklike" onClick={() => setOpen(!open)}>
            {open ? "Hide the prices" : "Show the prices"}
          </button>
        </>
      )}

      {tasks.length > 0 && (
        <p className="note">
          Each job is priced in its own unit —{" "}
          {tasks.map((t) => `${t.label} in ${t.natural_unit}`).join(", ")}. The
          Console refuses any other, because a minute of audio priced per 1,000
          tokens produces a plausible wrong number rather than an error.
        </p>
      )}
    </section>
  );
}
