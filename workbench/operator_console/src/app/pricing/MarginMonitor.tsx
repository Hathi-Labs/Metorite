// The margin monitor — what each tier ACTUALLY earned, against its own floor.
//
// Spec: `project-docs/specs/credit_pricing.md` §4.3. Migration 029.
//
// 🔴 **The screen that earns its keep.** The rest of the pricing board is data
// entry: it records what we intend to charge. This one says whether the
// intention survived contact with real traffic, which is the only question a
// price ever has to answer.
//
// ⚠️ **Every number here can be NULL, and NULL is NEUTRAL.** No saved credit
// price means no realised margin. No `tier_margin` row means no floor and no
// suggestion. Neither is a failure, and drawing either as a zero would report
// a fact nobody established.
//
// ⚠️ A SERVER component: every judgement is pure (`lib/priceboard.ts`).

import type { AiCatalog } from "@/lib/contract";
import {
  costBasis,
  costBasisLabel,
  marginPct,
  marginTone,
  monitorRows,
} from "@/lib/priceboard";
import { chipClass } from "@/lib/tone";

export default function MarginMonitor({ catalog }: { catalog: AiCatalog }) {
  const rows = monitorRows(catalog.tierMargins);
  const priced = catalog.creditPrice !== null;
  const anyFloor = rows.some((r) => r.marginFloor !== null);
  const alarms = rows.filter(
    (r) => marginTone(r.realisedMargin, r.marginFloor) === "alarm",
  );

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Margin monitor</h2>
        <p>
          What each tier <strong>actually earned</strong> over the last seven
          days, against the floor it was given. The panels above record what we
          intend to charge. This one says whether the intention survived
          contact with real traffic.
        </p>
      </div>

      {/* 🔴 Two different silences, and they need different sentences. The
          first is "nobody set the numbers". The second is "nobody can compute
          them". Reporting either as a healthy monitor would be a lie of
          omission. */}
      {!anyFloor && (
        <p className="field-hint warn">
          <strong>No tier has a margin floor yet</strong>, so nothing here can
          alarm. The floors and the multipliers are a commercial decision
          (H-42) and <code>tier_margin</code> ships empty on purpose — an agent
          builds this monitor, the owner sets the numbers it watches.
        </p>
      )}
      {!priced && (
        <p className="field-hint warn">
          <strong>No credit price is saved</strong>, so no realised margin can
          be computed at all. Credits and provider dollars are different units,
          and this console will not invent an exchange rate to bridge them.
          Save the credit price above and these figures fill in.
        </p>
      )}
      {alarms.length > 0 && (
        <div className="banner danger">
          <strong>
            {alarms.length} {alarms.length === 1 ? "tier is" : "tiers are"}{" "}
            below their own floor
          </strong>{" "}
          — {alarms.map((a) => a.tier).join(", ")}. Each tier is judged against
          the floor it was given, because the same margin means different
          things on a cheap tier and an expensive one.
        </div>
      )}

      {rows.length === 0 ? (
        <p className="field-hint">
          No tier has traffic or a margin set. Nothing to watch yet.
        </p>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th>Tier</th>
              <th>Calls</th>
              <th>Costed</th>
              {/* 🔴 Migration 031. Whether the Earned figure beside it is a
                  measurement or our own arithmetic. Without this the two look
                  identical on the page, and only one of them can go stale. */}
              <th>Basis</th>
              <th>Earned</th>
              <th>Floor</th>
              <th>Intended ×</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const tone = marginTone(r.realisedMargin, r.marginFloor);
              const basis = costBasis(r);
              return (
                <tr key={r.tier}>
                  <td className="mono">{r.tier}</td>
                  <td className="mono">{r.calls}</td>
                  {/* ⚠️ How much of the tier the margin speaks for. A figure
                      from two costed calls out of a thousand is not wrong, but
                      it is not evidence either. */}
                  <td
                    className={
                      r.calls > 0 && r.costedCalls < r.calls ? "warn-t" : "muted"
                    }
                  >
                    {r.costedCalls}
                  </td>
                  {/* ⚠️ A dash when nothing was costed. There is no figure to
                      qualify, and a word there would imply one exists. */}
                  <td>
                    {basis === "none" ? (
                      <span className="muted">—</span>
                    ) : (
                      <span
                        className={chipClass(
                          basis === "measured"
                            ? "ok"
                            : basis === "mixed"
                              ? "warn"
                              : "neutral",
                        )}
                        title={costBasisLabel(basis)}
                      >
                        {basis}
                      </span>
                    )}
                  </td>
                  <td>
                    {tone === "muted" ? (
                      <span className="muted">—</span>
                    ) : (
                      <span className={chipClass(tone === "alarm" ? "danger" : "ok")}>
                        {marginPct(r.realisedMargin)}
                      </span>
                    )}
                  </td>
                  <td className="muted">{marginPct(r.marginFloor)}</td>
                  <td className="muted mono">
                    {r.marginMultiplier === null ? "—" : `${r.marginMultiplier}×`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
