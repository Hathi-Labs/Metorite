"use client";

// The pricing cockpit — what we charge, what it costs us, and the gap.
//
// 🔴 **Until now there was NO way to set a price through the console.** The
// Console route, the BFF proxy and the elevation gate all existed, and no
// component called them — H-42 ("price the card") meant curl. The owner asked
// for the pricing story to be clear end to end; this panel is the downstream
// half. The upstream half (recording what a call cost us) is migration 013.
//
// ⚠️ **The two assumptions are typed in, used for arithmetic, and STORED
// NOWHERE.** There is no credit price in this system (H-42 is the owner's
// commercial act), so margins shown here run on the operator's own ₹/credit
// and ₹/$ figures. They live in component state, die on reload, never reach a
// fetch body, and the page says so beside them. `pricing.test.ts` fences it.
//
// ⚠️ **Vendor price and customer price never share a column.** "We pay" is
// USD per 1M from the profile; "we charge" is credits per 1k from the card.
// The margin column is the only place they meet, and it is derived, labelled,
// and dashed whenever either side is unknown.

import { useMemo, useState } from "react";

import { describeRate } from "@/lib/catalog";
import type { CatalogModel, ModelRate, Task } from "@/lib/contract";
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

export default function RateCard({
  rates,
  tasks,
  models,
}: {
  rates: ModelRate[];
  tasks: Task[];
  models: CatalogModel[];
}) {
  const [open, setOpen] = useState(false);
  const [inrPerCredit, setInrPerCredit] = useState("");
  const [inrPerUsd, setInrPerUsd] = useState("");

  // The price form.
  const [formOpen, setFormOpen] = useState(false);
  const [model, setModel] = useState("");
  const [task, setTask] = useState("chat");
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

  const unpriced = rates.filter((r) => r.mode === "unpriced").length;
  const byId = useMemo(
    () => new Map(models.map((m) => [m.id, m])),
    [models],
  );

  // Only models DECLARED for the picked task may be priced from here — the
  // same allowlist rule the tier form follows, and for the same reason: a
  // card for a model the Router will never accept is a draft nobody can run.
  const offerable = useMemo(
    () =>
      models
        .filter((m) => m.declared && (m.kinds as string[]).includes(task))
        .map((m) => m.id)
        .sort(),
    [models, task],
  );
  const picked = byId.get(model);
  const unit = tasks.find((t) => t.slug === task)?.natural_unit ?? "tokens";
  const tokenPriced = unit.includes("token");

  /** The margin cell for one card row. */
  function rowMargin(r: ModelRate): number | null {
    const m = byId.get(r.model);
    if (!m || r.mode !== "priced") return null;
    // Judged on the INPUT leg. One number an operator can compare down the
    // column; the output leg usually carries a similar or better ratio.
    return marginFraction(Number(r.inputPer1k) || null, m.inputPer1M, a);
  }

  function suggest(vendorPer1M: number | null): string {
    return roundCredits(priceForMargin(vendorPer1M, a, 0.7));
  }

  async function save() {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/operator/catalog/rates", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          model: model.trim(),
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
          not what the vendor charges us. Setting a number is a commercial
          decision, and it needs an elevated admin session.
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
          Used only for the margins on this page. Stored nowhere, sent
          nowhere — the real credit price is a separate commercial decision
          (H-42).
        </span>
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
                    <th>We pay (in, $/1M)</th>
                    <th>Margin</th>
                  </tr>
                </thead>
                <tbody>
                  {rates.map((r) => {
                    const m = byId.get(r.model);
                    const frac = rowMargin(r);
                    return (
                      <tr key={`${r.model}/${r.task}`}>
                        <td className="mono">{r.model}</td>
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
            <div className="field">
              <label htmlFor="rate-task">Job</label>
              <select
                id="rate-task"
                value={task}
                onChange={(e) => { setTask(e.target.value); setModel(""); }}
              >
                {tasks.map((t) => (
                  <option key={t.slug} value={t.slug}>{t.label}</option>
                ))}
              </select>
            </div>
            <div className="field grow">
              <label htmlFor="rate-model">Model</label>
              <select
                id="rate-model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              >
                <option value="">— pick a declared model —</option>
                {offerable.map((id) => (
                  <option key={id} value={id}>{id}</option>
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

          {picked && (
            <p className="muted small">
              We pay {picked.inputPer1M ?? "—"} in / {picked.outputPer1M ?? "—"} out
              {picked.cachedInputPer1M !== null && ` / ${picked.cachedInputPer1M} cached`}{" "}
              $ per 1M.
              {usable(a) && picked.inputPer1M !== null && (
                <>
                  {" "}That is{" "}
                  {roundCredits(vendorCostCreditsPer1k(picked.inputPer1M, a))} credits
                  per 1k in — at a 70% margin, charge about{" "}
                  <strong>{suggest(picked.inputPer1M)}</strong> in /{" "}
                  <strong>{suggest(picked.outputPer1M)}</strong> out.
                </>
              )}
            </p>
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
                    placeholder={picked ? suggest(picked.inputPer1M) : ""}
                  />
                  {picked && (
                    <span className="field-hint">
                      margin{" "}
                      {marginLabelPct(
                        marginFraction(Number(inP) || null, picked.inputPer1M, a),
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
                    placeholder={picked ? suggest(picked.outputPer1M) : ""}
                  />
                  {picked && (
                    <span className="field-hint">
                      margin{" "}
                      {marginLabelPct(
                        marginFraction(Number(outP) || null, picked.outputPer1M, a),
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
                    placeholder={picked ? suggest(picked.cachedInputPer1M) : ""}
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
                ? "Absorbed is a DECISION: the model answers and deliberately bills nothing. It renders differently from unpriced everywhere, so a free-on-purpose model cannot be mistaken for a forgotten one."
                : "Unpriced keeps the card explicit about what has not been decided. Calls still answer and bill nothing."}
            </p>
          )}

          <div className="job-actions">
            <button type="button" disabled={busy || !model} onClick={save}>
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
