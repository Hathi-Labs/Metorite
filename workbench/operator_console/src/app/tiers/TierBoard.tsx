"use client";

// Tiers and their backups — what a customer picks, and what happens when it
// stops answering.
//
// 🔴 **This page exists because `/models` was doing two jobs.** The catalog and
// the tier bindings sat on one screen, so a reader had to know which of two
// questions they were answering before they could read anything. Finding a
// model is one job. Deciding what a tier runs on is another.
//
// ⚠️ Every judgement is imported from `@/lib/fallback`, never written inline.
// This app's suite carries no React renderer, so logic in JSX is untested by
// construction and `fallback.test.ts` is the fence.
//
// 🔴 **A backup step cannot be SAVED yet, and this page says so where the
// control is.** `tier_binding` holds one model per (tier, job) with no ordering
// column. Drawing an enabled "add a backup" button over a table that cannot
// store one would be a worse lie than not drawing it: the operator would use
// it, believe they were covered, and find out during an outage.

import { useMemo, useState } from "react";

import { categoricalChip, providerGlyph } from "@/lib/categorical";
import type { AiCatalog, Tier, TierJob } from "@/lib/contract";
import {
  type ChainContext,
  chainLabel,
  chainProblems,
  chainTone,
  orderedChain,
  outageHeadline,
  outageReport,
  tierNextStep,
  unusedModels,
} from "@/lib/fallback";
import { capableModelsFor } from "@/lib/readiness";
import { chipClass } from "@/lib/tone";

function Provider({ model }: { model: string }) {
  const p = model.includes("/") ? model.slice(0, model.indexOf("/")) : model;
  return (
    <span className={categoricalChip(p)} title={`Supplied by ${p}`}>
      <span className="glyph">{providerGlyph(p)}</span>
      {p}
    </span>
  );
}

export default function TierBoard({
  catalog,
  armed,
}: {
  catalog: AiCatalog;
  armed: string[];
}) {
  const { tiers, tasks, models } = catalog;
  const ctx: ChainContext = useMemo(() => ({ models, armed }), [models, armed]);

  const [down, setDown] = useState<string[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const step = tierNextStep(tiers, ctx);
  const spare = useMemo(() => unusedModels(tiers, models), [tiers, models]);
  const outcomes = useMemo(() => outageReport(tiers, down, ctx), [tiers, down, ctx]);
  const headline = outageHeadline(down, outcomes);

  // Every provider we could lose — the ones a chain actually names, not every
  // vendor in the world.
  const inUse = useMemo(
    () =>
      [
        ...new Set(
          tiers
            .flatMap((t) => t.jobs)
            .flatMap((j) => j.chain)
            .map((s) => (s.model.includes("/") ? s.model.split("/")[0] : s.model)),
        ),
      ].sort(),
    [tiers],
  );

  const taskLabel = (slug: string) =>
    tasks.find((t) => t.slug === slug)?.label ?? slug;

  const key = (t: string, task: string) => `${t}::${task}`;

  async function savePrimary(tier: string, task: string, model: string) {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/operator/catalog/bindings", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ tier, task, model }),
      });
      setResult({ ok: res.ok, text: await res.text() });
      if (res.ok) setEditing(null);
    } finally {
      setBusy(false);
    }
  }

  function Job({ tier, job }: { tier: Tier; job: TierJob }) {
    const steps = orderedChain(job);
    const problems = chainProblems(job, ctx);
    const tone = chainTone(problems);
    const open = editing === key(tier.slug, job.task);
    const options = capableModelsFor(
      models.flatMap((m) => m.kinds.map((k) => ({ model: m.id, task: k }))),
      job.task,
    );

    return (
      <div className="job">
        <div className="job-head">
          <span className="job-name">{taskLabel(job.task)}</span>
          <span className={chipClass(tone)}>{chainLabel(job, problems)}</span>
        </div>

        <ol className="chain">
          {steps.map((s, i) => (
            <li key={`${s.model}-${s.rank}`}>
              <span className="rank" aria-hidden="true">
                {i + 1}
              </span>
              <div className="chainbody">
                <span className="job-model">{s.model}</span>
                <Provider model={s.model} />
              </div>
              {i === 0 && (
                <button
                  type="button"
                  className="linklike"
                  onClick={() => {
                    setEditing(key(tier.slug, job.task));
                    setDraft(s.model);
                  }}
                >
                  Change
                </button>
              )}
            </li>
          ))}
        </ol>

        {open && (
          <div className="job-edit">
            <label htmlFor={`m-${tier.slug}-${job.task}`}>
              First choice for {taskLabel(job.task)}
            </label>
            <select
              id={`m-${tier.slug}-${job.task}`}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            >
              <option value="">Choose a model…</option>
              {options.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            {options.length === 0 && (
              <p className="field-hint">
                No model can do this job yet. Add one on the Models page first.
              </p>
            )}
            <div className="job-actions">
              <button
                type="button"
                disabled={busy || !draft}
                onClick={() => savePrimary(tier.slug, job.task, draft)}
              >
                Save
              </button>
              <button
                type="button"
                className="linklike"
                onClick={() => setEditing(null)}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {problems.length > 0 && (
          <ul className="problems">
            {problems.map((p) => (
              <li key={p.label}>
                <span className={chipClass(p.tone)}>{p.label}</span>
                <span className="muted small">{p.detail}</span>
              </li>
            ))}
          </ul>
        )}

        {/* ⚠️ Disabled, with the reason ON the control. A tooltip somebody has
            to hover for is not a reason they will read. */}
        <button
          type="button"
          className="linklike add-job"
          disabled
          title="Backups need a database change first"
        >
          + Add a backup — not possible yet
        </button>
      </div>
    );
  }

  return (
    <>
      <div className={`nextstep ${step.tone}`}>
        <span className="dot" aria-hidden="true" />
        <div>
          <h2>{step.title}</h2>
          <p>{step.detail}</p>
        </div>
      </div>

      {result && (
        <p className={result.ok ? "result ok" : "result err"}>
          {result.ok
            ? "Saved. The old model stays on record, so an old invoice still shows what it charged."
            : `The Console refused: ${result.text}`}
        </p>
      )}

      {/* ── The outage simulator ── */}
      <section className="panel">
        <div className="panel-head">
          <h2>What if a provider goes down?</h2>
          <p>
            Turn one off and see what our customers would lose. Nothing here
            changes anything — it is a question, not a switch.
          </p>
        </div>

        <div className="facetrow">
          {inUse.length === 0 ? (
            <span className="muted small">
              No tier points at anything yet, so there is nothing to lose.
            </span>
          ) : (
            inUse.map((p) => (
              <button
                key={p}
                type="button"
                className="facet"
                aria-pressed={down.includes(p)}
                onClick={() =>
                  setDown(down.includes(p) ? down.filter((x) => x !== p) : [...down, p])
                }
              >
                <span className="glyph">{providerGlyph(p)}</span>
                {p}
              </button>
            ))
          )}
        </div>

        <p className={`outage-line ${headline.tone}`}>{headline.text}</p>

        {down.length > 0 && (
          <ul className="outcomes">
            {outcomes
              .filter((o) => o.status !== "unaffected")
              .map((o) => (
                <li key={`${o.tier}::${o.task}`} className={o.status}>
                  <span className="chip">{o.tier}</span>
                  <span className="muted small">{taskLabel(o.task)}</span>
                  {o.status === "down" && (
                    <span className={chipClass("danger")}>stops completely</span>
                  )}
                  {o.status === "failover" && (
                    <span className={chipClass("warn")}>moves to {o.after}</span>
                  )}
                  {o.status === "already-broken" && (
                    <span className={chipClass("neutral")}>
                      already not working — not this outage
                    </span>
                  )}
                </li>
              ))}
          </ul>
        )}
      </section>

      {/* ── The tiers themselves ── */}
      {tiers.length === 0 ? (
        <div className="empty">
          <h2>No tiers yet</h2>
          <p className="muted">
            A tier is a speed and quality setting a customer picks — Fast,
            Balanced or Powerful. Until one points at a model, every AI request
            fails.
          </p>
        </div>
      ) : (
        <div className="tier-grid">
          {tiers.map((t) => (
            <section className="tier-card" key={t.slug}>
              <header>
                <h3>{t.label}</h3>
                <span className="muted small">
                  {t.jobs.length === 0
                    ? "nothing set"
                    : `${t.jobs.length} job${t.jobs.length === 1 ? "" : "s"}`}
                </span>
              </header>
              {t.blurb && <p className="muted small">{t.blurb}</p>}
              {t.jobs.map((j) => (
                <Job key={j.task} tier={t} job={j} />
              ))}
            </section>
          ))}
        </div>
      )}

      {/* ⚠️ A `note`, not a banner. Nothing here is broken — it is capacity we
          are not selling, and drawing it at alarm weight is how a real alarm
          stops being read. */}
      {spare.length > 0 && (
        <p className="note">
          {spare.length} model{spare.length === 1 ? "" : "s"} can do work no
          tier uses: {spare.slice(0, 6).join(", ")}
          {spare.length > 6 ? `, and ${spare.length - 6} more` : ""}. Nothing is
          broken — it is capacity we are not selling.
        </p>
      )}

      <p className="note">
        Changing a model keeps the old one on record, so an old invoice still
        shows what it charged. You need an open elevation window to save.
      </p>
    </>
  );
}
