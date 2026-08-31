"use client";

// Price from cost — the METHOD, as a board (owner ask, 2026-08-30).
//
// 🔴 **Three steps, one knob.** The saved credit price fixes what a credit
// is worth. Each bound (tier, job)'s COST is its primary model's vendor
// price, converted to credits in the job's own unit. The suggested charge
// is cost ÷ (1 − margin) — so image generation comes out dearer than chat
// BECAUSE it costs more, not because somebody remembered to type a bigger
// number. Apply writes the real card through the same elevated-admin route
// as the manual form.
//
// ⚠️ **No fake numbers.** A costs-blind model gets no suggestion and a
// pointer to record its vendor price.
//
// ⚠️ **A per-unit job READS its cost from the model's profile** (H-78,
// §6A.11a clauses 5-7, 2026-08-31). `recordedVendorUsd` picks the column the
// task prices in — per minute for `transcribe`, per character for `speak`,
// per image for `image` — and the box opens on that number. The box still
// accepts a typed figure, because a model nobody has profiled still has no
// cost. *(This read "the feed does not carry those columns yet" until
// 2026-08-31. Migration 019 added them and the seam now fills them.)*
//
// ⚠️ Judgements live in `lib/priceboard.ts`; `priceboard.test.ts` fences.

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import type { AiCatalog } from "@/lib/contract";
import {
  boundJobs,
  parseMarginPct,
  recordedVendorUsd,
  savedAssumptions,
  tokenSuggestion,
  unitSuggestion,
  vendorUsdBox,
} from "@/lib/priceboard";
import { singular } from "@/lib/catalog";
import { chipClass, pricingTone } from "@/lib/tone";

export default function PriceFromCost({ catalog }: { catalog: AiCatalog }) {
  const router = useRouter();
  const [marginPct, setMarginPct] = useState("70");
  const [vendorUsd, setVendorUsd] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const a = savedAssumptions(catalog.creditPrice);
  const margin = parseMarginPct(marginPct);

  const jobs = useMemo(() => boundJobs(catalog), [catalog]);
  const modelById = useMemo(
    () => new Map(catalog.models.map((m) => [m.id, m])),
    [catalog.models],
  );
  const rateOf = useMemo(
    () => new Map(catalog.tierRates.map((r) => [`${r.tier}::${r.task}`, r])),
    [catalog.tierRates],
  );

  async function apply(body: Record<string, string>, key: string) {
    setBusy(key);
    setResult(null);
    try {
      const res = await fetch("/api/operator/catalog/tier-rates", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const text = await res.text();
      setResult({
        ok: res.ok,
        text: res.ok
          ? `Priced ${body.tier}. The card takes effect now.`
          : `The Console refused: ${text}`,
      });
      if (res.ok) router.refresh();
    } catch {
      setResult({
        ok: false,
        text: "The Console did not answer. The price did not save — check the network and try again.",
      });
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Price from cost</h2>
        <p>
          The method: each job&apos;s cost is its <strong>first model&apos;s
          vendor price</strong>, in credits. The suggested charge is cost ÷
          (1 − margin), so a dear capability prices dear by construction.
          Applying writes the real card and needs an elevated admin session.
        </p>
      </div>

      {a === null ? (
        <p className="resultline">
          Save the credit price above first. The method turns vendor dollars
          into credits, and a credit with no rupee value cannot be derived
          from a dollar cost.
        </p>
      ) : (
        <>
          <div className="assumptions">
            <label>
              Target margin&nbsp;%
              <input
                inputMode="numeric"
                value={marginPct}
                onChange={(e) => setMarginPct(e.target.value)}
              />
            </label>
            <span className="muted small">
              The share of the customer&apos;s price that is ours, after the
              vendor is paid. 70% means a job costing 0.3 credits charges 1.
            </span>
          </div>

          {jobs.length === 0 && (
            <p className="muted small">
              Nothing is bound yet — point the tiers at models first, then
              price them here.
            </p>
          )}

          {jobs.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Tier · job</th>
                    <th>Primary model</th>
                    <th>Suggested charge</th>
                    <th>Today</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((j) => {
                    const key = `${j.tier}::${j.task}`;
                    const m = modelById.get(j.primary);
                    const current = rateOf.get(key) ?? null;
                    const sugg =
                      j.tokenPriced && m && margin !== null
                        ? tokenSuggestion(m, a, margin)
                        : null;
                    const recorded = recordedVendorUsd(j.task, m);
                    const usd = vendorUsdBox(vendorUsd[key], recorded);
                    const perUnit =
                      !j.tokenPriced && margin !== null
                        ? unitSuggestion(Number(usd) > 0 ? Number(usd) : null, a, margin)
                        : "";
                    return (
                      <tr key={key}>
                        <td>
                          <span className="mono">{j.tier}</span>
                        </td>
                        <td className="mono">{j.primary}</td>
                        <td>
                          {j.tokenPriced ? (
                            sugg ? (
                              `${sugg.in1k} in / ${sugg.out1k} out per 1k`
                            ) : (
                              <span className="muted small">
                                no usable vendor price (unknown, or listed
                                at $0) — <a href="/models">fetch the vendor
                                feed</a> and copy its facts onto the model,
                                or price the job in the hand form below
                              </span>
                            )
                          ) : (
                            <span className="unitsugg">
                              <label>
                                vendor $ per {singular(j.unit)}
                                <input
                                  inputMode="decimal"
                                  value={usd}
                                  onChange={(e) =>
                                    setVendorUsd({
                                      ...vendorUsd,
                                      [key]: e.target.value,
                                    })
                                  }
                                />
                              </label>
                              {perUnit && (
                                <span>
                                  → {perUnit} per {singular(j.unit)}
                                </span>
                              )}
                              {vendorUsd[key] === undefined &&
                                recorded !== null && (
                                  <span className="muted small">
                                    from the model&apos;s recorded vendor cost
                                  </span>
                                )}
                            </span>
                          )}
                        </td>
                        <td>
                          {current === null || current.mode === "unpriced" ? (
                            <span className={chipClass("warn")}>no price</span>
                          ) : (
                            <span className={chipClass(pricingTone(current.mode))}>
                              {current.mode}
                            </span>
                          )}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="linklike"
                            disabled={
                              busy !== null ||
                              margin === null ||
                              (j.tokenPriced ? sugg === null : !perUnit)
                            }
                            onClick={() =>
                              apply(
                                {
                                  tier: j.tier,
                                  task: j.task,
                                  unit: j.unit,
                                  pricing_mode: "priced",
                                  input_per_1k: sugg?.in1k ?? "0",
                                  output_per_1k: sugg?.out1k ?? "0",
                                  cached_input_per_1k: sugg?.cached1k ?? "0",
                                  credits_per_unit: perUnit || "0",
                                },
                                key,
                              )
                            }
                          >
                            {busy === key ? "Applying…" : "Apply"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {result && (
            <p className={result.ok ? "result ok" : "result err"}>
              {result.text}
            </p>
          )}

          <p className="muted small">
            Suggestions read the PRIMARY model only (D67): a failover moves
            our cost, never the customer&apos;s price. Round the applied
            number later if you want friendlier figures — repricing is an
            insert, and history stays.
          </p>
        </>
      )}
    </section>
  );
}
