"use client";

// "Try image analysis" — the panel on the `tier-vision` card (VOX-4, §31.5).
//
// 🔴 **How an operator proves a vision binding before an app depends on it.**
// It calls `POST /api/operator/catalog/vision/try`, never the customer door,
// so it spends the platform key and lands in no customer's usage.
//
// ⚠️ Every rule is in `@/lib/voiceTry`. This file is composition only.

import { useState } from "react";

import { costText } from "@/lib/decide";
import {
  DEFAULT_QUESTION,
  MAX_QUESTION_CHARS,
  type VisionResult,
  readVisionResult,
  refusalText,
  visionBlocker,
  visionForm,
} from "@/lib/voiceTry";

export default function TryVision() {
  const [file, setFile] = useState<File | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<VisionResult | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const blocker = visionBlocker(file, question)?.message ?? null;

  async function send() {
    if (!file) return;
    setBusy(true);
    setResult(null);
    setRefusal(null);
    try {
      const res = await fetch("/api/operator/catalog/vision/try", {
        method: "POST",
        body: visionForm(file, question),
      });
      const text = await res.text();
      if (!res.ok) {
        setRefusal(refusalText(res.status, text));
        return;
      }
      let parsed: unknown = null;
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = null;
      }
      const read = readVisionResult(parsed);
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
      <summary>Try image analysis</summary>
      <p className="field-hint">
        Upload one image (png, jpeg, gif or webp, up to 4 MB), ask a question
        about it, and read the answer of the bound model. This uses the
        platform key. It writes one audit row and no customer usage.
      </p>

      <div className="field">
        <label htmlFor="try-image">Image</label>
        <input
          id="try-image"
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setResult(null);
            setRefusal(null);
          }}
        />
      </div>

      <div className="field">
        <label htmlFor="try-vision-question">Question</label>
        <input
          id="try-vision-question"
          placeholder={DEFAULT_QUESTION}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        {question.trim().length > MAX_QUESTION_CHARS && (
          <p className="field-hint warn">
            {question.trim().length} of {MAX_QUESTION_CHARS} characters.
          </p>
        )}
      </div>

      <div className="job-actions">
        <button
          type="button"
          className="secondary"
          disabled={busy || blocker !== null}
          title={blocker ?? "Send this image to the bound model."}
          onClick={send}
        >
          {busy ? "Looking…" : "Ask"}
        </button>
      </div>
      {blocker && file && <p className="field-hint">{blocker}</p>}

      {refusal && <p className="result err">{refusal}</p>}
      {result && (
        <div className="try-result">
          <p className="try-answer">
            <span className="lbl">Answer</span> {result.text || "(the model sent no text)"}
          </p>
          <div className="stats">
            <div className="stat">
              <div className="lbl">Latency</div>
              <div className="num small-num">{result.latencyMs} ms</div>
            </div>
            <div className="stat">
              <div className="lbl">Input tokens</div>
              <div className="num small-num">{result.inputTokens}</div>
            </div>
            <div className="stat">
              <div className="lbl">Output tokens</div>
              <div className="num small-num">{result.outputTokens}</div>
            </div>
            <div className="stat">
              <div className="lbl">Vendor cost</div>
              <div className="num small-num mono">{costText(result.vendorCostUsd)}</div>
            </div>
          </div>
          <p className="muted small">
            Served by <span className="mono">{result.model}</span>. One audit row
            records this call. No customer usage row does.
          </p>
        </div>
      )}
    </details>
  );
}
