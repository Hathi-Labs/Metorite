"use client";

// The pricing cockpit — what a CUSTOMER pays, per tier. D67, migration 015.
//
// 🔴 **This panel is the /pricing page's heart** (owner IA directive,
// 2026-08-30 — it lived on /models, then /tiers, as the price key moved).
// The customer buys a TIER; the model is our supply. /tiers answers "what
// serves"; this page answers "what do we charge, and what do we keep".
//
// ⚠️ **The two assumption boxes seed from the SAVED credit price (017)
// and then belong to the operator.** Overtyping them is a what-if:
// component state, dead on reload, never in a fetch body —
// `pricing.test.ts` fences it. Saving a new FACT is the CreditPrice
// panel's explicit POST, never a side effect of exploring here.
//
// ⚠️ **Margin is judged against the PRIMARY model of the chain.** The tier's
// price holds while a failover serves a different model, so the margin on a
// failover day differs from the number shown here — usually in our favour,
// and /usage carries the per-call truth either way.

import { useMemo, useState } from "react";

import { describeRate } from "@/lib/catalog";
import type { AiCatalog, TierRate } from "@/lib/contract";
import {
  type Assumptions,
  marginFraction,
  marginLabelPct,
  parseAssumption,
  priceForMargin,
  roundCredits,
  usable,
  vendorCostCreditsPer1k,
} from "@/lib/pricing";
import { chipClass, pricingTone, type Tone } from "@/lib/tone";

/** The colour a margin wears. Negative sells below cost and must shout. */
function marginTone(f: number | null): Tone {
  if (f === null) return "neutral";
  if (f < 0) return "danger";
  if (f < 0.3) return "warn";
  return "ok";
}

/** "1.500000" → "1.5" — the wire is exact, the input box is for humans. */
function seed(s: string | undefined): string {
  return (s ?? "").replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

export default function TierPricing({ catalog }: { catalog: AiCatalog }) {
  const { tiers, tierRates, tasks, models, creditPrice } = catalog;
  // Open by default: on its own page, the price table IS the page.
  const [open, setOpen] = useState(true);
  const [inrPerCredit, setInrPerCredit] = useState(
    seed(creditPrice?.inrPerCredit));
  const [inrPerUsd, setInrPerUsd] = useState(seed(creditPrice?.usdToInr));

  // The price form. D68: the tier decides its ONE job — no job picker.
  const [formOpen, setFormOpen] = useState(false);
  const [tier, setTier] = useState("");
  const [mode, setMode] = useState("priced");
  const [inP, setInP] = useState("");
  const [outP, setOutP] = useState("");
  const [cachedP, setCachedP] = useState("");
  const [perUnit, setPerUnit] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const a: Assumptions = {
    inrPerCredit: parseAssumption(inrPerCredit),
    inrPerUsd: parseAssumption(inrPerUsd),
  };

  const modelById = useMemo(
    () => new Map(models.map((m) => [m.id, m])),
    [models],
  );

  /** The model that answers first for one (tier, job) — margin's yardstick. */
  const primaryOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const t of tiers) {
      for (const j of t.jobs) {
        if (j.chain.length > 0) m.set(`${t.slug}::${j.task}`, j.chain[0].model);
      }
    }
    return m;
  }, [tiers]);

  // Bound jobs with no decided price: they answer customers and bill NOTHING.
  const boundUnpriced = useMemo(() => {
    const decided = new Set(
      tierRates.filter((r) => r.mode !== "unpriced")
        .map((r) => `${r.tier}::${r.task}`),
    );
    return [...primaryOf.keys()].filter((k) => !decided.has(k));
  }, [tierRates, primaryOf]);

  // ⚠️ Only REGISTERED, CATEGORISED tiers are priceable — the Console
  // refuses both a ghost and the wrong kind of job (D68), so the picker
  // never offers either.
  const priceable = useMemo(
    () => tiers.filter((t) => t.registered && t.task),
    [tiers],
  );

  // D68: the tier's category IS the job being priced.
  const task = priceable.find((t) => t.slug === tier)?.task ?? "chat";
  const primary = modelById.get(primaryOf.get(`${tier}::${task}`) ?? "");
  const unit = tasks.find((t) => t.slug === task)?.natural_unit ?? "tokens";
  const tokenPriced = unit.includes("token");

  /** The margin cell for one card row, judged on the input leg of the
   *  chain's PRIMARY model. One number an operator can compare down the
   *  column; the output leg usually carries a similar or better ratio. */
  function rowMargin(r: TierRate): number | null {
    if (r.mode !== "priced") return null;
    const m = modelById.get(primaryOf.get(`${r.tier}::${r.task}`) ?? "");
    if (!m) return null;
    return marginFraction(Number(r.inputPer1k) || null, m.inputPer1M, a);
  }

  function suggest(vendorPer1M: number | null): string {
    return roundCredits(priceForMargin(vendorPer1M, a, 0.7));
  }

  async function save() {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/operator/catalog/tier-rates", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          tier,
          task,
          unit,
          pricing_mode: mode,
          input_per_1k: inP.trim() || "0",
          output_per_1k: outP.trim() || "0",
          cached_input_per_1k: cachedP.trim() || "0",
          credits_per_unit: perUnit.trim() || "0",
        }),
      });
      const text = await res.text();
      setResult({
        ok: res.ok,
        text: res.ok
          ? "Priced. The card takes effect now; past calls stay on the card they were rated by."
          : `The Console refused: ${text}`,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>What we charge</h2>
        <p>
          The customer&apos;s price, in credits, per unit of each job —{" "}
          not what the vendor charges us. It is keyed on the{" "}
          <strong>tier they picked</strong>, never on the model that served
          them (D67): a failover changes our cost, not their price. Setting a
          number is a commercial decision, and it needs an elevated admin
          session.
        </p>
      </div>

      {/* ── The operator's assumptions, for arithmetic only ── */}
      <div className="assumptions">
        <label>
          One credit is ₹
          <input
            inputMode="decimal"
            value={inrPerCredit}
            onChange={(e) => setInrPerCredit(e.target.value)}
            placeholder="1"
          />
        </label>
        <label>
          One dollar is ₹
          <input
            inputMode="decimal"
            value={inrPerUsd}
            onChange={(e) => setInrPerUsd(e.target.value)}
            placeholder="88"
          />
        </label>
        <span className="muted small">
          {creditPrice
            ? "Seeded from the saved credit price. Overtyping is a " +
              "what-if: it moves the margins on this page and nothing else."
            : "No credit price is saved yet (H-42) — save one above. " +
              "Until then these hand-typed figures drive the margins, " +
              "and they are stored nowhere."}
        </span>
      </div>

      {boundUnpriced.length > 0 && (
        <p className="resultline">
          {boundUnpriced.length} bound{" "}
          {boundUnpriced.length === 1 ? "job has" : "jobs have"} no price.
          They will answer customers and charge nothing.
        </p>
      )}

      {tierRates.length > 0 && (
        <>
          {open && (
            <div style={{ overflowX: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th>Tier</th>
                    <th>Job</th>
                    <th>State</th>
                    <th>Charged</th>
                    <th>Primary pays (in, $/1M)</th>
                    <th>Margin</th>
                  </tr>
                </thead>
                <tbody>
                  {tierRates.map((r) => {
                    const m = modelById.get(
                      primaryOf.get(`${r.tier}::${r.task}`) ?? "");
                    const frac = rowMargin(r);
                    return (
                      <tr key={`${r.tier}/${r.task}`}>
                        <td className="mono">{r.tier}</td>
                        <td>{tasks.find((t) => t.slug === r.task)?.label ?? r.task}</td>
                        <td>
                          <span className={chipClass(pricingTone(r.mode))}>{r.mode}</span>
                        </td>
                        <td>{describeRate(r)}</td>
                        <td>{m?.inputPer1M ?? "—"}</td>
                        <td>
                          <span className={chipClass(marginTone(frac))}>
                            {marginLabelPct(frac)}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <button type="button" className="linklike" onClick={() => setOpen(!open)}>
            {open ? "Hide the prices" : "Show the prices"}
          </button>
        </>
      )}

      {/* ── Set a price ── */}
      {formOpen ? (
        <div className="setup">
          <div className="formrow">
            <div className="field grow">
              <label htmlFor="rate-tier">Tier</label>
              <select
                id="rate-tier"
                value={tier}
                onChange={(e) => setTier(e.target.value)}
              >
                <option value="">— pick a tier —</option>
                {priceable.map((t) => (
                  <option key={t.slug} value={t.slug}>
                    {t.slug} — {tasks.find((x) => x.slug === t.task)?.label ?? t.task}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="rate-mode">State</label>
              <select id="rate-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="priced">priced — bills credits</option>
                <option value="absorbed">absorbed — free on purpose</option>
                <option value="unpriced">unpriced — not decided yet</option>
              </select>
            </div>
          </div>

          {tier && (
            primary ? (
              <p className="muted small">
                The first choice for this job is <span className="mono">{primary.id}</span>,
                which costs us {primary.inputPer1M ?? "—"} in /{" "}
                {primary.outputPer1M ?? "—"} out $ per 1M.
                {usable(a) && primary.inputPer1M !== null && (
                  <>
                    {" "}That is{" "}
                    {roundCredits(vendorCostCreditsPer1k(primary.inputPer1M, a))} credits
                    per 1k in — at a 70% margin, charge about{" "}
                    <strong>{suggest(primary.inputPer1M)}</strong> in /{" "}
                    <strong>{suggest(primary.outputPer1M)}</strong> out.
                  </>
                )}
              </p>
            ) : (
              <p className="muted small">
                Nothing is bound to this job yet. Pricing it now is allowed —
                the price waits for a chain, and nothing bills until both
                exist.
              </p>
            )
          )}

          {mode === "priced" && (
            tokenPriced ? (
              <div className="formrow">
                <div className="field">
                  <label htmlFor="rate-in">Credits per 1k in</label>
                  <input
                    id="rate-in"
                    inputMode="decimal"
                    value={inP}
                    onChange={(e) => setInP(e.target.value)}
                    placeholder={primary ? suggest(primary.inputPer1M) : ""}
                  />
                  {primary && (
                    <span className="field-hint">
                      margin{" "}
                      {marginLabelPct(
                        marginFraction(Number(inP) || null, primary.inputPer1M, a),
                      )}
                    </span>
                  )}
                </div>
                <div className="field">
                  <label htmlFor="rate-out">Credits per 1k out</label>
                  <input
                    id="rate-out"
                    inputMode="decimal"
                    value={outP}
                    onChange={(e) => setOutP(e.target.value)}
                    placeholder={primary ? suggest(primary.outputPer1M) : ""}
                  />
                  {primary && (
                    <span className="field-hint">
                      margin{" "}
                      {marginLabelPct(
                        marginFraction(Number(outP) || null, primary.outputPer1M, a),
                      )}
                    </span>
                  )}
                </div>
                <div className="field">
                  <label htmlFor="rate-cached">Credits per 1k cached in</label>
                  <input
                    id="rate-cached"
                    inputMode="decimal"
                    value={cachedP}
                    onChange={(e) => setCachedP(e.target.value)}
                    placeholder={primary ? suggest(primary.cachedInputPer1M) : ""}
                  />
                </div>
              </div>
            ) : (
              <div className="field">
                <label htmlFor="rate-unit">Credits per {unit.replace(/^1k /, "1,000 ")}</label>
                <input
                  id="rate-unit"
                  inputMode="decimal"
                  value={perUnit}
                  onChange={(e) => setPerUnit(e.target.value)}
                />
              </div>
            )
          )}

          {mode !== "priced" && (
            <p className="field-hint">
              {mode === "absorbed"
                ? "Absorbed is a DECISION: the job answers and deliberately bills nothing. It renders differently from unpriced everywhere, so a free-on-purpose job cannot be mistaken for a forgotten one."
                : "Unpriced keeps the card explicit about what has not been decided. Calls still answer and bill nothing."}
            </p>
          )}

          <div className="job-actions">
            <button type="button" disabled={busy || !tier} onClick={save}>
              {busy ? "Saving…" : "Set the price"}
            </button>
            <button type="button" className="linklike" onClick={() => setFormOpen(false)}>
              Close
            </button>
          </div>

          {result && (
            <p className={result.ok ? "result ok" : "result err"}>{result.text}</p>
          )}

          <p className="muted small">
            A price is an INSERT with a later date, never an edit — a past
            invoice stays readable against the card that produced it. Repricing
            is saving again.
          </p>
        </div>
      ) : (
        <button type="button" className="setupbtn" onClick={() => setFormOpen(true)}>
          Set a price
        </button>
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
