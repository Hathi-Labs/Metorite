"use client";

// The model catalog's write surface (CP-10 slice 3, rebuilt WS-31).
//
// ⚠️ **The GAPS come first, above everything.** `model_capability` says what a
// model CAN do; `tier_binding` says what we USE it for; `model_rate_card` says
// what we CHARGE. No single table shows the difference, and the difference is
// where the mistakes live. An operator scrolling three tables to diff them by
// eye will not do it.
//
// 🔴 **What changed, and why.** The three tables were all correct and still
// answered the wrong question. They said "what is configured"; an operator
// with a customer waiting asks "what can I sell". So the readiness matrix now
// sits above them — one cell per (tier, task), coloured by whether that pair
// is servable AND priced — and the tables became tabs underneath, because
// three of them stacked is a scroll, not a comparison.
//
// ⚠️ Every judgement here is computed in `@/lib/readiness`, never inline. This
// app's suite carries no React renderer, so logic written in JSX is untested
// by construction — `readiness.test.ts` is the fence that makes the matrix
// mean something.

import { useState } from "react";

import { describeRate } from "@/lib/catalog";
import { buildMatrix, readinessLine, type CellState } from "@/lib/readiness";
import { chipClass, pricingTone } from "@/lib/tone";

export type Catalog = {
  tasks: { slug: string; label: string; natural_unit: string }[];
  capabilities: {
    model: string;
    task: string;
    invocation: string;
    streams: boolean;
  }[];
  bindings: {
    tier: string;
    task: string;
    model: string;
    effective_from: string | null;
  }[];
  rates: {
    model: string;
    task: string;
    unit: string;
    pricing_mode: string;
    input_per_1k: string;
    output_per_1k: string;
    cached_input_per_1k: string;
    credits_per_unit: string;
    effective_from: string | null;
  }[];
  unbound: { model: string; task: string }[];
  unserved: { model: string; task: string }[];
};

type Result = { ok: boolean; text: string } | null;
type Tab = "bindings" | "capabilities" | "rates";

async function post(path: string, body: unknown): Promise<Result> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return { ok: res.ok, text: await res.text() };
}

// A refusal is relayed VERBATIM — the Console is the authority, and
// paraphrasing it here would invent a second vocabulary for the same 400.
function ResultLine({ result }: { result: Result }) {
  if (!result) return null;
  return (
    <p className={result.ok ? "result ok" : "result err"}>
      {result.ok ? "Done. Reload to see it." : `The Console refused: ${result.text}`}
    </p>
  );
}

// What a matrix cell says about itself, in an operator's words rather than a
// state name. The title is the whole explanation — a coloured square that does
// not say why is a puzzle, not a signal.
const CELL_COPY: Record<CellState, { label: string; tone: string; why: string }> = {
  ready: { label: "servable", tone: "ok", why: "Capable and priced." },
  broken: {
    label: "cannot serve",
    tone: "danger",
    why: "Bound to a model with no capability for this task. The Router 500s on the first request.",
  },
  unpriced: {
    label: "unpriced",
    tone: "warn",
    why: "Servable, but no rate card. It will run and bill nothing.",
  },
  empty: { label: "not bound", tone: "", why: "No tier binding for this pair." },
};

export default function CatalogAdmin({ data }: { data: Catalog }) {
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("bindings");

  // Capability
  const [capModel, setCapModel] = useState("");
  const [capTask, setCapTask] = useState("chat");
  const [capVerb, setCapVerb] = useState("acompletion");
  const [capStreams, setCapStreams] = useState(false);

  // Binding
  const [bindTierName, setBindTierName] = useState("");
  const [bindTask, setBindTask] = useState("chat");
  const [bindModel, setBindModel] = useState("");

  async function send(path: string, body: unknown) {
    setBusy(true);
    setResult(null);
    try {
      setResult(await post(path, body));
    } finally {
      setBusy(false);
    }
  }

  const unitOf = (task: string) =>
    data.tasks.find((t) => t.slug === task)?.natural_unit ?? "";

  const matrix = buildMatrix(data);

  return (
    <>
      {/* ── The two gaps, above everything ── */}
      {data.unserved.length > 0 ? (
        <div className="banner danger">
          <strong>Bound but not capable.</strong> The Router resolves a model
          for these and then cannot decide which provider verb to call — a 500
          on the first request. Declare the capability, or re-point the tier.
          <ul>
            {data.unserved.map((g) => (
              <li key={`${g.model}/${g.task}`}>
                <span className="mono">{g.model}</span> · {g.task}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.unbound.length > 0 ? (
        <div className="banner info">
          <strong>Capable, and not used.</strong> These models declare a task no
          tier points at. Nothing is broken — it is capacity we are not selling.
          <ul>
            {data.unbound.map((g) => (
              <li key={`${g.model}/${g.task}`}>
                <span className="mono">{g.model}</span> · {g.task}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <ResultLine result={result} />

      {/* ── Readiness: the question an operator actually has ── */}
      <section className="panel">
        <div className="panel-head">
          <h2>Can we serve it?</h2>
          <p>{readinessLine(matrix)}</p>
        </div>

        <div className="stats">
          <div className={`stat ${matrix.counts.ready > 0 ? "good" : ""}`}>
            <div className="num">{matrix.counts.ready}</div>
            <div className="lbl">Servable</div>
          </div>
          <div className={`stat ${matrix.counts.broken > 0 ? "alarm" : ""}`}>
            <div className="num">{matrix.counts.broken}</div>
            <div className="lbl">Cannot serve</div>
          </div>
          <div className={`stat ${matrix.counts.unpriced > 0 ? "caution" : ""}`}>
            <div className="num">{matrix.counts.unpriced}</div>
            <div className="lbl">Unpriced</div>
          </div>
          <div className="stat">
            <div className="num">{matrix.tiers.length}</div>
            <div className="lbl">Tiers</div>
          </div>
        </div>

        {matrix.tiers.length === 0 ? (
          <div className="empty">
            <h2>Nothing is bound yet</h2>
            <p className="muted">
              A tier is created by pointing it at a model. Until one exists, the
              Router has nothing to resolve and every AI request fails.
            </p>
          </div>
        ) : (
          <div className="matrix-scroll">
            <table className="matrix">
              <thead>
                <tr>
                  <th />
                  {matrix.tasks.map((t) => (
                    <th key={t.slug}>
                      {t.label}
                      <div className="muted small">{t.natural_unit}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrix.rows.map((row) => (
                  <tr key={row.tier}>
                    <td className="tier-name">{row.tier}</td>
                    {row.cells.map((c) => {
                      const copy = CELL_COPY[c.state];
                      const cls =
                        c.state === "broken"
                          ? "is-broken"
                          : c.state === "unpriced"
                            ? "is-unpriced"
                            : c.state === "empty"
                              ? "is-empty"
                              : "";
                      return (
                        <td key={c.task} className={cls} title={copy.why}>
                          {c.model ? (
                            <>
                              <span className="cell-model">{c.model}</span>
                              <span className="cell-flags">
                                <span className={chipClass(copy.tone as never)}>
                                  {copy.label}
                                </span>
                              </span>
                            </>
                          ) : (
                            <span className="muted small">not bound</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── The three tables, as tabs ── */}
      <section className="panel">
        <div className="tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "bindings"}
            onClick={() => setTab("bindings")}
          >
            Tier bindings<span className="count">{data.bindings.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "capabilities"}
            onClick={() => setTab("capabilities")}
          >
            Capabilities<span className="count">{data.capabilities.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "rates"}
            onClick={() => setTab("rates")}
          >
            Rate card<span className="count">{data.rates.length}</span>
          </button>
        </div>

        {tab === "bindings" ? (
          <div role="tabpanel">
            <div className="panel-head" style={{ marginTop: 14 }}>
              <h2>What each tier runs on</h2>
              <p>
                In force now. Superseded rows stay in the table for the audit
                trail and are not shown here.
              </p>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Tier</th>
                  <th>Model</th>
                  <th>In force since</th>
                </tr>
              </thead>
              <tbody>
                {data.bindings.map((b) => (
                  <tr key={`${b.task}/${b.tier}`}>
                    <td>{b.task}</td>
                    <td>
                      <span className="chip">{b.tier}</span>
                    </td>
                    <td className="mono">{b.model}</td>
                    <td className="muted">{b.effective_from ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>Point a tier at a model</h3>
            <p className="note">
              An append, never an edit — the old row stays so a past invoice can
              still be read against what it was charged on. Needs an elevation
              window.
            </p>
            <div className="formrow">
              <div className="field">
                <label htmlFor="bind-tier">Tier</label>
                <input
                  id="bind-tier"
                  aria-label="Tier"
                  placeholder="tier-fast"
                  value={bindTierName}
                  onChange={(e) => setBindTierName(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="bind-task">Task</label>
                <select
                  id="bind-task"
                  aria-label="Task for the binding"
                  value={bindTask}
                  onChange={(e) => setBindTask(e.target.value)}
                >
                  {data.tasks.map((t) => (
                    <option key={t.slug} value={t.slug}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field grow">
                <label htmlFor="bind-model">Model</label>
                <input
                  id="bind-model"
                  aria-label="Model for the binding"
                  placeholder="deepseek/deepseek-chat"
                  value={bindModel}
                  onChange={(e) => setBindModel(e.target.value)}
                />
              </div>
              <button
                type="button"
                disabled={busy || !bindTierName || !bindModel}
                onClick={() =>
                  send("/api/operator/catalog/bindings", {
                    tier: bindTierName,
                    task: bindTask,
                    model: bindModel,
                  })
                }
              >
                Bind
              </button>
            </div>
            <p className="note">
              This task is priced in {unitOf(bindTask)}.
            </p>
          </div>
        ) : null}

        {tab === "capabilities" ? (
          <div role="tabpanel">
            <div className="panel-head" style={{ marginTop: 14 }}>
              <h2>What each model can do</h2>
              <p>
                The provider verb for each (model, task). This is data, not a
                hardcoded set — a model that cannot be described here cannot be
                served.
              </p>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Task</th>
                  <th>Verb</th>
                  <th>Streams</th>
                </tr>
              </thead>
              <tbody>
                {data.capabilities.map((c) => (
                  <tr key={`${c.model}/${c.task}`}>
                    <td className="mono">{c.model}</td>
                    <td>{c.task}</td>
                    <td className="mono">{c.invocation}</td>
                    <td>
                      <span className={chipClass(c.streams ? "ok" : "neutral")}>
                        {c.streams ? "yes" : "no"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h3>Declare one</h3>
            <div className="formrow">
              <div className="field grow">
                <label htmlFor="cap-model">Model</label>
                <input
                  id="cap-model"
                  aria-label="Model"
                  placeholder="openai/gpt-4o"
                  value={capModel}
                  onChange={(e) => setCapModel(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="cap-task">Task</label>
                <select
                  id="cap-task"
                  aria-label="Task"
                  value={capTask}
                  onChange={(e) => setCapTask(e.target.value)}
                >
                  {data.tasks.map((t) => (
                    <option key={t.slug} value={t.slug}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="cap-verb">Provider verb</label>
                <select
                  id="cap-verb"
                  aria-label="Provider verb"
                  value={capVerb}
                  onChange={(e) => setCapVerb(e.target.value)}
                >
                  {[
                    "acompletion",
                    "aembedding",
                    "atranscription",
                    "aspeech",
                    "aimage_generation",
                  ].map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>
                  <input
                    type="checkbox"
                    checked={capStreams}
                    onChange={(e) => setCapStreams(e.target.checked)}
                  />
                  streams
                </label>
              </div>
              <button
                type="button"
                disabled={busy || !capModel}
                onClick={() =>
                  send("/api/operator/catalog/capabilities", {
                    model: capModel,
                    task: capTask,
                    invocation: capVerb,
                    streams: capStreams,
                  })
                }
              >
                Declare
              </button>
            </div>
          </div>
        ) : null}

        {tab === "rates" ? (
          <div role="tabpanel">
            <div className="panel-head" style={{ marginTop: 14 }}>
              <h2>Rate card</h2>
              <p>
                🔴 Every card ships <strong>unpriced</strong>, and setting a real
                number is a commercial decision, not an operational one. The
                prices below are what customers are billed.
              </p>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Task</th>
                  <th>Mode</th>
                  <th>Unit</th>
                  <th>Rate</th>
                </tr>
              </thead>
              <tbody>
                {data.rates.map((r) => (
                  <tr key={`${r.model}/${r.task}`}>
                    <td className="mono">{r.model}</td>
                    <td>{r.task}</td>
                    <td>
                      <span className={chipClass(pricingTone(r.pricing_mode))}>
                        {r.pricing_mode}
                      </span>
                    </td>
                    <td>{r.unit}</td>
                    <td>{describeRate(r)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">
              A task is priced in its own unit — {data.tasks
                .map((t) => `${t.slug} in ${t.natural_unit}`)
                .join(", ")}
              . The Console refuses any other, because a minute of audio priced
              per 1,000 tokens produces a plausible wrong number rather than an
              error.
            </p>
          </div>
        ) : null}
      </section>
    </>
  );
}
