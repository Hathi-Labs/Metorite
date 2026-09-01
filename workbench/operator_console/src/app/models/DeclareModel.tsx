"use client";

// Adding a model to the catalog — the one write this page still owns.
//
// ⚠️ **Folded into a `<details>`, closed by default.** Declaring a model is a
// rare act and reading the catalog is a constant one. An always-open form at
// the top of the page pushes the thing people came for below the fold.
//
// ⚠️ **A refusal is relayed VERBATIM.** The Console knows the model-id shape
// rule and the verb rule. Paraphrasing here would give one 400 two vocabularies.

import { useState } from "react";

import type { ProviderAccount, Task } from "@/lib/contract";
import { vendorWarning } from "@/lib/readiness";

const VERBS = [
  "acompletion",
  "aembedding",
  "atranscription",
  "aspeech",
  "aimage_generation",
];

export default function DeclareModel({
  tasks,
  accounts = [],
  accountsKnown = true,
}: {
  tasks: Task[];
  accounts?: ProviderAccount[];
  /** False when the credential read failed — the no-key warning would then
   *  be a claim from absent evidence, so it stays quiet. */
  accountsKnown?: boolean;
}) {
  const [model, setModel] = useState("");
  const [task, setTask] = useState(tasks[0]?.slug ?? "chat");
  const [verb, setVerb] = useState("acompletion");
  const [streams, setStreams] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  const warning = accountsKnown ? vendorWarning(model, accounts) : null;

  async function declare() {
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/operator/catalog/capabilities", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ model: model.trim(), task, invocation: verb, streams }),
      });
      setResult({ ok: res.ok, text: await res.text() });
      if (res.ok) setModel("");
    } catch {
      setResult({
        ok: false,
        text: "The Console did not answer. Nothing declared — check the network and try again.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="advanced">
      <summary>Add a model to the catalog</summary>

      <p className="field-hint">
        A model has to be declared before any tier can use it. The provider verb
        is how the Router calls it — chat models use <span className="mono">
        acompletion</span>.
      </p>

      <div className="formrow">
        <div className="field grow">
          <label htmlFor="cap-model">Model id</label>
          <input
            id="cap-model"
            placeholder="openai/gpt-4o"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
          {warning && <span className="field-hint warn">{warning}</span>}
        </div>
        <div className="field">
          <label htmlFor="cap-task">What it does</label>
          <select id="cap-task" value={task} onChange={(e) => setTask(e.target.value)}>
            {tasks.map((t) => (
              <option key={t.slug} value={t.slug}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="cap-verb">Provider verb</label>
          <select id="cap-verb" value={verb} onChange={(e) => setVerb(e.target.value)}>
            {VERBS.map((v) => (
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
              checked={streams}
              onChange={(e) => setStreams(e.target.checked)}
            />
            answers a word at a time
          </label>
        </div>
        <button type="button" disabled={busy || !model.trim()} onClick={declare}>
          Add
        </button>
      </div>

      {result && (
        <p className={result.ok ? "result ok" : "result err"}>
          {result.ok
            ? "Added. Reload to see it in the list."
            : `The Console refused: ${result.text}`}
        </p>
      )}
    </details>
  );
}
