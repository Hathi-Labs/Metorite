"use client";

// Set a price BY HAND — the manual lane of /pricing. D67, migration 015.
//
// 🔴 **Slimmed on the owner's read of the page (2026-08-30).** This panel
// used to carry what-if assumption boxes and its own margin table, and
// with the saved credit price (017), the price list and the price-from-
// cost board above, both had become echoes — the owner's exact words were
// "is this section repeated?". What is left is the ONE thing only this
// form can do: name any price yourself, or mark a job absorbed (free on
// purpose) or unpriced — the modes the method board never writes.
//
// ⚠️ **All arithmetic runs on the SAVED credit price.** No local boxes:
// one frame, saved once, read everywhere — exploring is the margin knob
// above. The margin hints here judge the input leg of the chain's PRIMARY
// model (D67): a failover moves our cost, never the customer's price.

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import type { AiCatalog } from "@/lib/contract";
import { savedAssumptions } from "@/lib/priceboard";
import {
  type Assumptions,
  marginFraction,
  marginLabelPct,
  priceForMargin,
  roundCredits,
  usable,
  vendorCostCreditsPer1k,
} from "@/lib/pricing";

export default function TierPricing({ catalog }: { catalog: AiCatalog }) {
  const { tiers, tasks, models, creditPrice } = catalog;
  const router = useRouter();

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

  // The SAVED frame or nothing — hints go quiet rather than guessing.
  const a: Assumptions =
    savedAssumptions(creditPrice) ?? { inrPerCredit: null, inrPerUsd: null };

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

  function suggest(vendorPer1M: number | null): string {
    return roundCredits(priceForMargin(vendorPer1M, a, 0.7));
  }

  async function save() {
    // ⚠️ A blank box is NOT a zero. This form once coerced every blank to
    // "0", so a skipped cached leg silently billed cache hits free — the
    // opposite of the "unknown never bills as free" rule. A priced card
    // must state every leg; typing 0 is the explicit free-on-purpose act.
    if (mode === "priced") {
      const blank = tokenPriced
        ? [inP, outP, cachedP].some((v) => !v.trim())
        : !perUnit.trim();
      if (blank) {
        setResult({
          ok: false,
          text:
            "Every box needs a number. Type 0 to bill that leg free on " +
            "purpose — a blank box is not a decision. Cached leg unknown? " +
            "Charge the full input rate.",
        });
        return;
      }
    }
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
      // The price list and the method board above read this card — a save
      // that claims "takes effect now" must show its effect now.
      if (res.ok) router.refresh();
    } catch {
      setResult({
        ok: false,
        text: "The Console did not answer. The price did not save — check the network and try again.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Set a price by hand</h2>
        <p>
          The manual lane: name the customer&apos;s price yourself —{" "}
          not what the vendor charges us — or mark a job{" "}
          <strong>absorbed</strong> (free on purpose) or{" "}
          <strong>unpriced</strong>, which the board above never writes. The
          price stays keyed on the tier they picked (D67), and saving needs
          an elevated admin session.
        </p>
      </div>

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
