"use client";

// The model catalog's write surface (CP-10 slice 3).
//
// ⚠️ **The GAPS come first, above the tables.** `model_capability` says what a
// model CAN do; `tier_binding` says what we USE it for. Neither table shows the
// difference, and the difference is where the mistakes live. An operator
// scrolling two tables to diff them by eye will not do it.

import { useState } from "react";

import { describeRate } from "@/lib/catalog";

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
    <p className={result.ok ? "muted" : "banner"}>
      {result.ok ? "Done. Reload to see it." : `The Console refused: ${result.text}`}
    </p>
  );
}

export default function CatalogAdmin({ data }: { data: Catalog }) {
  const [result, setResult] = useState<Result>(null);
  const [busy, setBusy] = useState(false);

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

  return (
    <>
      {/* ── The two gaps ── */}
      {data.unserved.length > 0 ? (
        <div className="banner">
          <strong>Bound but not capable.</strong> The Router resolves a model
          for these and then cannot decide which provider verb to call — a 500
          on the first request. Declare the capability, or re-point the tier.
          <ul>
            {data.unserved.map((g) => (
              <li key={`${g.model}/${g.task}`}>
                {g.model} · {g.task}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {data.unbound.length > 0 ? (
        <section className="panel">
          <h2>Capable, and not used</h2>
          <p className="muted">
            These models declare a task no tier points at. Nothing is broken —
            it is capacity we are not selling.
          </p>
          <ul>
            {data.unbound.map((g) => (
              <li key={`${g.model}/${g.task}`}>
                {g.model} · {g.task}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <ResultLine result={result} />

      {/* ── Bindings in force ── */}
      <section className="panel">
        <h2>Tier bindings</h2>
        <p className="muted">
          What each (task, tier) pair runs on, in force now. Superseded rows
          stay in the table for the audit trail and are not shown here.
        </p>
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
                <td>{b.tier}</td>
                <td>{b.model}</td>
                <td className="muted">{b.effective_from ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3>Point a tier at a model</h3>
        <p className="muted">
          An append, never an edit — the old row stays so a past invoice can
          still be read against what it was charged on. Needs an elevation
          window.
        </p>
        <div className="row">
          <input
            aria-label="Tier"
            placeholder="tier-fast"
            value={bindTierName}
            onChange={(e) => setBindTierName(e.target.value)}
          />
          <select
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
          <input
            aria-label="Model for the binding"
            placeholder="deepseek/deepseek-chat"
            value={bindModel}
            onChange={(e) => setBindModel(e.target.value)}
          />
          <button
            type="button"
            className="linklike"
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
      </section>

      {/* ── Capabilities ── */}
      <section className="panel">
        <h2>What each model can do</h2>
        <p className="muted">
          The provider verb for each (model, task). This is data, not a
          hardcoded set — a model that cannot be described here cannot be
          served.
        </p>
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
                <td>{c.model}</td>
                <td>{c.task}</td>
                <td>{c.invocation}</td>
                <td>{c.streams ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3>Declare one</h3>
        <div className="row">
          <input
            aria-label="Model"
            placeholder="openai/gpt-4o"
            value={capModel}
            onChange={(e) => setCapModel(e.target.value)}
          />
          <select
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
          <select
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
          <label>
            <input
              type="checkbox"
              checked={capStreams}
              onChange={(e) => setCapStreams(e.target.checked)}
            />{" "}
            streams
          </label>
          <button
            type="button"
            className="linklike"
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
      </section>

      {/* ── Rates ── */}
      <section className="panel">
        <h2>Rate card</h2>
        <p className="muted">
          🔴 Every card ships <strong>unpriced</strong>, and setting a real
          number is a commercial decision, not an operational one. The prices
          below are what customers are billed.
        </p>
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
                <td>{r.model}</td>
                <td>{r.task}</td>
                <td>{r.pricing_mode}</td>
                <td>{r.unit}</td>
                <td>{describeRate(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">
          A task is priced in its own unit — {data.tasks
            .map((t) => `${t.slug} in ${t.natural_unit}`)
            .join(", ")}
          . The Console refuses any other, because a minute of audio priced per
          1,000 tokens produces a plausible wrong number rather than an error.
          {bindTask ? ` This task is priced in ${unitOf(bindTask)}.` : ""}
        </p>
      </section>
    </>
  );
}
