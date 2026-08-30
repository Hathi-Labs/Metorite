// The price list — what a customer pays TODAY, in plain words.
//
// 🔴 **This is the "clear pricing" surface the owner asked for
// (2026-08-30).** One row per tier on the slate, grouped exactly as the
// tier board groups them, each row saying its in-force price in the tier's
// own unit — and in rupees, when the credit price is saved. A tier with no
// card says "no price yet" in amber, because it answers customers and
// bills nothing.
//
// ⚠️ A SERVER component: every judgement is pure (`lib/priceboard.ts`,
// `lib/catalog.ts`) and every fact is the page's own catalog read.

import { describeRate } from "@/lib/catalog";
import type { AiCatalog } from "@/lib/contract";
import { inrRateLine, priceGroups } from "@/lib/priceboard";
import { chipClass, pricingTone } from "@/lib/tone";

export default function PriceList({ catalog }: { catalog: AiCatalog }) {
  const groups = priceGroups(catalog);
  if (groups.length === 0) return null;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>The price list</h2>
        <p>
          What a customer pays today, per tier, in the tier&apos;s own unit —
          read from the card billing reads, so this list cannot disagree with
          an invoice. Rupee figures use the saved credit price.
        </p>
      </div>
      {groups.map((g) => (
        <div className="pricegroup" key={g.title}>
          <h3>{g.title}</h3>
          <ul className="pricelist">
            {g.rows.map(({ tier, rate }) => {
              const inr = rate ? inrRateLine(rate, catalog.creditPrice, catalog.tasks) : null;
              return (
                <li key={tier.slug}>
                  <span className="pl-name">
                    <b>{tier.label}</b>
                    <span className="chip mono">{tier.slug}</span>
                  </span>
                  <span className="pl-price">
                    {rate === null || rate.mode === "unpriced" ? (
                      <span
                        className={chipClass("warn")}
                        title="Answers customers and bills nothing until priced"
                      >
                        no price yet
                      </span>
                    ) : rate.mode === "absorbed" ? (
                      <span className={chipClass(pricingTone(rate.mode))}>
                        free on purpose
                      </span>
                    ) : (
                      <>
                        <span>{describeRate(rate)}</span>
                        {inr && <span className="muted small">{inr}</span>}
                      </>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}
