"use client";

// "Try a decision" — the panel on the `tier-decide` card (CP-13b, §6A.14).
//
// 🔴 **How an operator proves a binding before an app depends on it.** It
// calls `POST /api/operator/catalog/decide/try`, never the customer door, so
// it spends the platform key and lands in no customer's usage.
//
// ⚠️ Every rule is in `@/lib/decide`. This file is composition only.

import { useState } from "react";

import {
  QUESTION_TYPE_LABEL,
  QUESTION_TYPES,
  type CriterionDraft,
  type QuestionType,
  type TryResult,
  readTryResult,
  tryBlocker,
  tryBody,
} from "@/lib/decide";
import DecisionResult from "./DecisionResult";

const EMPTY_ROWS: CriterionDraft[] = [
  { key: "", text: "" },
  { key: "", text: "" },
];

export default function TryDecision() {
  const [state, setState] = useState("");
  const [type, setType] = useState<QuestionType>("boolean");
  const [instructions, setInstructions] = useState("");
  const [criteria, setCriteria] = useState<CriterionDraft[]>(EMPTY_ROWS);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TryResult | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const draft = { state, type, instructions, criteria };
  const blocker = tryBlocker(draft);
  const needsCriteria = type !== "boolean";

  const setRow = (i: number, row: CriterionDraft) =>
    setCriteria(criteria.map((r, j) => (j === i ? row : r)));

  async function send() {
    setBusy(true);
    setResult(null);
    setRefusal(null);
    try {
      const res = await fetch("/api/operator/catalog/decide/try", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(tryBody(draft)),
      });
      const text = await res.text();
      if (!res.ok) {
        setRefusal(`The Console refused: ${text}`);
        return;
      }
      let parsed: unknown = null;
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = null;
      }
      const read = readTryResult(parsed);
      if (read) setResult(read);
      else setRefusal(`The Console answered a shape this page cannot read: ${text}`);
    } catch {
      setRefusal("The Console did not answer. Check the network and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="advanced try-decision">
      <summary>Try a decision</summary>
      <p className="field-hint">
        Ask one question about a state, and see what the bound model answers,
        how long it took and what it cost us. This uses the platform key. It
        writes one audit row and no customer usage.
      </p>

      <div className="field">
        <label htmlFor="try-state">State</label>
        <textarea
          id="try-state"
          rows={4}
          placeholder="From: a@b.example — Subject: pumps — Sixteen pumps are overdue."
          value={state}
          onChange={(e) => setState(e.target.value)}
        />
      </div>

      <div className="formrow">
        <div className="field grow">
          <label htmlFor="try-question">Question</label>
          <input
            id="try-question"
            placeholder="Is this an unsolicited sales email?"
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="try-type">Answer type</label>
          <select
            id="try-type"
            value={type}
            onChange={(e) => setType(e.target.value as QuestionType)}
          >
            {QUESTION_TYPES.map((t) => (
              <option key={t} value={t}>
                {QUESTION_TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </div>
      </div>

      <fieldset className="try-criteria">
        <legend>
          {needsCriteria
            ? type === "choice"
              ? "Options (key, and what it means)"
              : "Levels, lowest first (key, and what it means)"
            : "What yes and no mean (optional, key true or false)"}
        </legend>
        {/* One header, then one line per row. A label on every row
            repeated "Key" and "Meaning" down the whole card. */}
        <div className="try-row try-row-head" aria-hidden="true">
          <span>Key</span>
          <span>Meaning</span>
        </div>
        {criteria.map((row, i) => (
          <div className="try-row" key={i}>
            <input
              id={`try-key-${i}`}
              className="mono"
              aria-label={`Key ${i + 1}`}
              placeholder={i === 0 ? (type === "boolean" ? "true" : "low") : ""}
              value={row.key}
              onChange={(e) => setRow(i, { ...row, key: e.target.value })}
            />
            <input
              id={`try-text-${i}`}
              aria-label={`Meaning ${i + 1}`}
              value={row.text}
              onChange={(e) => setRow(i, { ...row, text: e.target.value })}
            />
          </div>
        ))}
        <div className="job-actions">
          <button
            type="button"
            className="secondary"
            onClick={() => setCriteria([...criteria, { key: "", text: "" }])}
          >
            Add a row
          </button>
          {criteria.length > 1 && (
            <button
              type="button"
              className="secondary"
              onClick={() => setCriteria(criteria.slice(0, -1))}
            >
              Remove the last row
            </button>
          )}
        </div>
      </fieldset>

      <div className="job-actions">
        <button
          type="button"
          className="secondary"
          disabled={busy || blocker !== null}
          title={blocker ?? "Send this question to the bound model."}
          onClick={send}
        >
          {busy ? "Asking…" : "Ask"}
        </button>
      </div>
      {blocker && <p className="field-hint">{blocker}</p>}

      {refusal && <p className="result err">{refusal}</p>}
      {result && <DecisionResult result={result} />}
    </details>
  );
}
