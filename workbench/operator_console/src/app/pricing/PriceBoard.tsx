"use client";

// The price board — one card per tier, and the whole decision on that card.
//
// 🔴 **This replaces four panels** (`PriceList`, `PriceFromCost`,
// `TierPricing`, `MarginMonitor`). They split ONE question — "what do we
// charge for this tier, and is it enough?" — across a read-only list, a
// suggestion table, a hand form and a monitor. An operator pricing Fast read
// it in the first and acted in the second or the third, with nothing joining
// them, and the same unsaved credit price was reported in three of them.
//
// 🔴 **From-cost and by-hand were never two things.** They write the same
// card, through the same route, with the same body. One pre-fills the boxes
// and the other does not, so here the suggestion is a FILL BUTTON inside the
// one editor, and the operator can still type over it.
//
// ⚠️ **Every judgement is imported from `@/lib/priceRows`.** This app carries
// no React renderer, so logic in JSX is untested by construction, and
// `priceRows.test.ts` is the fence.
//
// ⚠️ **PER MILLION throughout, and the POST says so.** `input_per_1m` and its
// two siblings are the scale of record (2026-09-04). The Console still accepts
// the per-1k fields and multiplies them by 1000, so sending both scales would
// be a silent disagreement — this sends only the per-million fields.

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import type { AiCatalog, TierRate } from "@/lib/contract";
import { HELP_PRICING } from "@/lib/help";
import { savedAssumptions, inrLabel, marginPct, parseMarginPct, costBasis, costBasisLabel, defaultMarginPct } from "@/lib/priceboard";
import { marginLabelPct, roundCredits } from "@/lib/pricing";
import {
  type TierPriceRow,
  plannedMargin,
  priceState,
  pricingAlert,
  rowCost,
  suggestFor,
  tierPriceRows,
} from "@/lib/priceRows";
import { singular } from "@/lib/catalog";
import { chipClass } from "@/lib/tone";

type Mode = "priced" | "absorbed" | "unpriced";

export default function PriceBoard({ catalog }: { catalog: AiCatalog }) {
  const router = useRouter();
  const groups = useMemo(() => tierPriceRows(catalog), [catalog]);
  const a = savedAssumptions(catalog.creditPrice);
  const alert = pricingAlert(groups, catalog.creditPrice);

  const [open, setOpen] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  if (groups.length === 0) {
    return (
      <section className="panel">
        <div className="panel-head">
          <h2>What each tier costs, and what we charge</h2>
          <p>
            No tier is registered yet, so there is nothing to price. Tiers are
            created on <a href="/tiers">Tiers &amp; backups</a>.
          </p>
        </div>
      </section>
    );
  }

  return (
    <>
      <section className={`panel alert-${alert.tone}`}>
        <div className="alertline">
          <span className={`dot ${alert.tone}`} aria-hidden="true" />
          <div>
            <b>{alert.title}</b>
            <p>{alert.detail}</p>
          </div>
        </div>
      </section>

      {result && (
        <p className={result.ok ? "result ok" : "result err"}>{result.text}</p>
      )}

      {groups.map((g) => (
        <section className="panel" key={g.title}>
          <div className="panel-head">
            <h2>{g.title}</h2>
            <p>
              What the vendor charges us for each tier&apos;s first model, what
              a customer pays, and what real traffic actually earned.
            </p>
          </div>
          <div className="tier-grid">
            {g.rows.map((r) => (
              <TierCard
                key={r.tier.slug}
                row={r}
                catalog={catalog}
                assumptions={a}
                open={open === r.tier.slug}
                onToggle={() =>
                  setOpen(open === r.tier.slug ? null : r.tier.slug)
                }
                onSaved={(text, ok) => {
                  setResult({ ok, text });
                  if (ok) {
                    setOpen(null);
                    router.refresh();
                  }
                }}
              />
            ))}
          </div>
        </section>
      ))}
    </>
  );
}

// ── One tier ───────────────────────────────────────────────────────────────

function TierCard({
  row,
  catalog,
  assumptions,
  open,
  onToggle,
  onSaved,
}: {
  row: TierPriceRow;
  catalog: AiCatalog;
  assumptions: ReturnType<typeof savedAssumptions>;
  open: boolean;
  onToggle: () => void;
  onSaved: (text: string, ok: boolean) => void;
}) {
  const state = priceState(row);
  const cost = rowCost(row, assumptions);
  const planned = plannedMargin(row, assumptions);
  const unit = singular(row.unit);

  const STATE_CHIP: Record<typeof state, { tone: Parameters<typeof chipClass>[0]; label: string; help: string }> = {
    unbound: { tone: "warn", label: "no model", help: HELP_PRICING.runsOn },
    unpriced: { tone: "warn", label: "no price yet", help: HELP_PRICING.modeUnpriced },
    absorbed: { tone: "accent", label: "free on purpose", help: HELP_PRICING.modeAbsorbed },
    priced: { tone: "ok", label: "priced", help: HELP_PRICING.weCharge },
  };
  const chip = STATE_CHIP[state];

  // The measured side. Null everywhere is the shipped state, and null is
  // NEUTRAL — a tier nobody has billed has nothing to report, not a zero.
  const m = row.margin;
  const realised = m?.realisedMargin ?? null;
  const floor = m?.marginFloor ?? null;
  const under =
    realised !== null && floor !== null && Number(realised) < Number(floor);
  const basis = m ? costBasis(m) : "none";

  return (
    <article className={`tier-card${under ? " under" : ""}`}>
      <header>
        <h3 title={row.tier.blurb || undefined}>{row.tier.label}</h3>
        <span className={chipClass(chip.tone)} title={chip.help}>
          {chip.label}
        </span>
      </header>

      <dl className="pricefacts">
        <dt title={HELP_PRICING.runsOn}>runs on</dt>
        <dd>
          {row.primaryId === null ? (
            <a href="/tiers">bind a model first →</a>
          ) : (
            <span className="mono">{row.primaryId}</span>
          )}
        </dd>

        <dt title={HELP_PRICING.costsUs}>costs us</dt>
        <dd>{costLine(cost, row, assumptions, catalog)}</dd>

        <dt title={HELP_PRICING.weCharge}>we charge</dt>
        <dd>{chargeLine(row, catalog)}</dd>

        <dt title={HELP_PRICING.plannedMargin}>margin</dt>
        <dd>
          {planned === null ? (
            <span className="muted">—</span>
          ) : (
            <>
              {marginLabelPct(planned)}{" "}
              <span className="muted small">of what they pay is ours</span>
            </>
          )}
        </dd>

        <dt title={HELP_PRICING.earned}>earned, 7 days</dt>
        <dd>
          {realised === null ? (
            <span className="muted">
              {m && m.calls > 0 ? "not costable yet" : "no traffic yet"}
            </span>
          ) : (
            <>
              <span className={under ? "danger-t" : undefined}>
                {marginPct(realised)}
              </span>
              {floor !== null && (
                <span className="muted small" title={HELP_PRICING.floor}>
                  {under ? " under its " : " above its "}
                  {marginPct(floor)} floor
                </span>
              )}
              {basis !== "none" && (
                <span className="muted small"> · {costBasisLabel(basis)}</span>
              )}
            </>
          )}
        </dd>
      </dl>

      <button
        type="button"
        className="linklike wide"
        onClick={onToggle}
        title={HELP_PRICING.setPrice}
        disabled={row.primaryId === null && state === "unbound" && !open}
      >
        {open ? "Close" : state === "priced" || state === "absorbed" ? "Change the price" : "Set the price"}
      </button>

      {open && (
        <PriceEditor
          row={row}
          catalog={catalog}
          assumptions={assumptions}
          onSaved={onSaved}
        />
      )}
    </article>
  );
}

/** What the vendor charges us, in the card's own scale and in rupees. */
function costLine(
  cost: { input: number | null; output: number | null },
  row: TierPriceRow,
  a: ReturnType<typeof savedAssumptions>,
  catalog: AiCatalog,
) {
  // ⚠️ A dash and a hover, never the sentence. This ran on every card,
  // so one missing credit price printed the same instruction eleven times
  // while the headline above already carried it once.
  if (a === null)
    return (
      <span className="muted" title={HELP_PRICING.creditValue}>
        —
      </span>
    );
  if (row.primaryId === null) return <span className="muted">—</span>;
  if (cost.input === null) {
    return (
      <span className="muted small">
        the vendor price is unknown — <a href="/models">record it on the model</a>
      </span>
    );
  }
  if (!row.tokenPriced) {
    const c = roundCredits(cost.input);
    return (
      <>
        {c} credits per {singular(row.unit)}{" "}
        <span className="muted small">({inrLabel(c, catalog.creditPrice)})</span>
      </>
    );
  }
  const i = roundCredits(cost.input);
  const o = cost.output === null ? "—" : roundCredits(cost.output);
  return (
    <>
      {i} in / {o} out per 1M{" "}
      <span className="muted small">
        ({inrLabel(i, catalog.creditPrice)} / {inrLabel(o, catalog.creditPrice)})
      </span>
    </>
  );
}

/** What a customer pays today. */
function chargeLine(row: TierPriceRow, catalog: AiCatalog) {
  const rate = row.rate;
  if (rate === null || rate.mode === "unpriced") {
    // ⚠️ MUTED, not amber. The chip in the card's header is already the
    // alarm for this state, and drawing it twice on one card spends the
    // page's only warning colour on a fact the reader has just read.
    return <span className="muted">nothing — this tier bills no one</span>;
  }
  if (rate.mode === "absorbed") {
    return <span>free on purpose, inside the seat price</span>;
  }
  if (row.tokenPriced) {
    return (
      <>
        {rate.inputPer1m} in / {rate.outputPer1m} out per 1M{" "}
        <span className="muted small">
          ({inrLabel(rate.inputPer1m, catalog.creditPrice)} /{" "}
          {inrLabel(rate.outputPer1m, catalog.creditPrice)})
        </span>
      </>
    );
  }
  return (
    <>
      {rate.creditsPerUnit} per {singular(row.unit)}{" "}
      <span className="muted small">
        ({inrLabel(rate.creditsPerUnit, catalog.creditPrice)})
      </span>
    </>
  );
}

// ── The one editor ─────────────────────────────────────────────────────────

function PriceEditor({
  row,
  catalog,
  assumptions,
  onSaved,
}: {
  row: TierPriceRow;
  catalog: AiCatalog;
  assumptions: ReturnType<typeof savedAssumptions>;
  onSaved: (text: string, ok: boolean) => void;
}) {
  const existing = row.rate;
  const [mode, setMode] = useState<Mode>(
    existing && (existing.mode === "absorbed" || existing.mode === "unpriced")
      ? (existing.mode as Mode)
      : "priced",
  );
  const [marginBox, setMarginBox] = useState(defaultMarginPct(catalog));
  const [inP, setInP] = useState(existing?.inputPer1m ?? "");
  const [outP, setOutP] = useState(existing?.outputPer1m ?? "");
  const [cachedP, setCachedP] = useState(existing?.cachedInputPer1m ?? "");
  const [perUnit, setPerUnit] = useState(existing?.creditsPerUnit ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const margin = parseMarginPct(marginBox);
  const sugg = suggestFor(row, assumptions, margin);

  function useSuggestion() {
    if (!sugg) return;
    if (row.tokenPriced) {
      setInP(sugg.input);
      setOutP(sugg.output);
      setCachedP(sugg.cached);
    } else {
      setPerUnit(sugg.input);
    }
  }

  async function save() {
    // ⚠️ A blank box is NOT a zero. Coercing a blank to "0" once billed a
    // skipped cached leg free — the opposite of "unknown never bills as free".
    if (mode === "priced") {
      const blank = row.tokenPriced
        ? [inP, outP, cachedP].some((v) => !v.trim())
        : !perUnit.trim();
      if (blank) {
        setErr(
          "Every box needs a number. Type 0 to bill that leg free on purpose — " +
            "a blank box is not a decision. Cached leg unknown? Charge the full " +
            "input rate.",
        );
        return;
      }
    }
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch("/api/operator/catalog/tier-rates", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          tier: row.tier.slug,
          task: row.task,
          unit: row.unit,
          pricing_mode: mode,
          // Per MILLION only. See the header — sending both scales would be a
          // disagreement the Console resolves silently in favour of this one.
          input_per_1m: inP.trim() || "0",
          output_per_1m: outP.trim() || "0",
          cached_input_per_1m: cachedP.trim() || "0",
          credits_per_unit: perUnit.trim() || "0",
        }),
      });
      const text = await res.text();
      onSaved(
        res.ok
          ? `${row.tier.label} is priced. It takes effect now, and past calls keep the card they were rated by.`
          : `The Console refused: ${text}`,
        res.ok,
      );
      if (!res.ok) setErr(`The Console refused: ${text}`);
    } catch {
      setErr(
        "The Console did not answer. The price did not save — check the network and try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="priceeditor">
      <div className="segmented" role="group" aria-label="How this tier bills">
        {(
          [
            ["priced", "Charge for it", HELP_PRICING.modePriced],
            ["absorbed", "Free on purpose", HELP_PRICING.modeAbsorbed],
            ["unpriced", "No price", HELP_PRICING.modeUnpriced],
          ] as [Mode, string, string][]
        ).map(([m, label, help]) => (
          <button
            key={m}
            type="button"
            title={help}
            aria-pressed={mode === m}
            onClick={() => setMode(m)}
          >
            {label}
          </button>
        ))}
      </div>

      {mode === "priced" && (
        <>
          <div className="suggestrow">
            <label title={HELP_PRICING.targetMargin}>
              Keep&nbsp;
              <input
                inputMode="numeric"
                value={marginBox}
                onChange={(e) => setMarginBox(e.target.value)}
                size={3}
              />
              &nbsp;% of the price
            </label>
            <button
              type="button"
              className="linklike"
              onClick={useSuggestion}
              disabled={!sugg}
              title={HELP_PRICING.useSuggestion}
            >
              {sugg
                ? row.tokenPriced
                  ? `Use ${sugg.input} in / ${sugg.output} out`
                  : `Use ${sugg.input} per ${singular(row.unit)}`
                : "No suggestion"}
            </button>
            {!sugg && (
              <span className="muted small">
                {assumptions === null
                  ? "a suggestion needs the credit price saved"
                  : margin === null
                    ? "type a margin between 1 and 95"
                    : "the vendor price for this tier's model is unknown"}
              </span>
            )}
          </div>

          {row.tokenPriced ? (
            <div className="legs">
              <Leg label="Input, per 1M" help={HELP_PRICING.inputPrice} value={inP} set={setInP} price={catalog} />
              <Leg label="Output, per 1M" help={HELP_PRICING.outputPrice} value={outP} set={setOutP} price={catalog} />
              <Leg label="Cached input, per 1M" help={HELP_PRICING.cachedPrice} value={cachedP} set={setCachedP} price={catalog} />
            </div>
          ) : (
            <div className="legs">
              <Leg
                label={`Credits per ${singular(row.unit)}`}
                help={HELP_PRICING.perUnitPrice}
                value={perUnit}
                set={setPerUnit}
                price={catalog}
              />
            </div>
          )}
        </>
      )}

      {mode === "absorbed" && (
        <p className="muted small">
          This tier will answer customers and put nothing on their invoice, on
          purpose. The board draws it differently from a tier nobody has
          priced, so the two never get confused.
        </p>
      )}
      {mode === "unpriced" && (
        <p className="muted small">
          This tier will answer customers and bill nothing, and the board will
          keep asking you to price it.
        </p>
      )}

      {err && <p className="result err">{err}</p>}

      <div className="editoractions">
        <button type="button" onClick={save} disabled={busy} title={HELP_PRICING.savePrice}>
          {busy ? "Saving…" : "Save the price"}
        </button>
        <span className="muted small">
          Saving is a commercial act, so it needs an elevated admin session.
        </span>
      </div>
    </div>
  );
}

function Leg({
  label,
  help,
  value,
  set,
  price,
}: {
  label: string;
  help: string;
  value: string;
  set: (v: string) => void;
  price: AiCatalog;
}) {
  const inr = value.trim() ? inrLabel(value.trim(), price.creditPrice) : null;
  return (
    <label title={help}>
      {label}
      <input inputMode="decimal" value={value} onChange={(e) => set(e.target.value)} />
      <span className="muted small">{inr ?? " "}</span>
    </label>
  );
}
