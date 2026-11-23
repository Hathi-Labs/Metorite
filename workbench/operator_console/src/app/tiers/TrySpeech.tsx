"use client";

// "Try speech to text" — the panel on the `tier-stt` card (VOX-4, §31.5).
//
// 🔴 **How an operator proves a speech binding before an app depends on it.**
// It calls `POST /api/operator/catalog/transcribe/try`, never the customer
// door, so it spends the platform key and lands in no customer's usage.
//
// ⚠️ Every rule is in `@/lib/voiceTry`. This file is composition only.

import { useState } from "react";

import {
  MAX_AUDIO_SECONDS,
  type TranscribeResult,
  audioForm,
  fileBlocker,
  readTranscribeResult,
  refusalText,
  secondsText,
} from "@/lib/voiceTry";

export default function TrySpeech() {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TranscribeResult | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const blocker = fileBlocker("audio", file)?.message ?? null;

  async function send() {
    if (!file) return;
    setBusy(true);
    setResult(null);
    setRefusal(null);
    try {
      const res = await fetch("/api/operator/catalog/transcribe/try", {
        method: "POST",
        body: audioForm(file),
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
      const read = readTranscribeResult(parsed);
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
      <summary>Try speech to text</summary>
      <p className="field-hint">
        Upload one WAV clip of up to {MAX_AUDIO_SECONDS} seconds, and read what
        the bound model hears. This uses the platform key. It writes one audit
        row and no customer usage. Sarvam takes WAV only.
      </p>

      <div className="field">
        <label htmlFor="try-clip">Clip (.wav)</label>
        <input
          id="try-clip"
          type="file"
          accept=".wav,audio/wav,audio/x-wav"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setResult(null);
            setRefusal(null);
          }}
        />
      </div>

      <div className="job-actions">
        <button
          type="button"
          className="secondary"
          disabled={busy || blocker !== null}
          title={blocker ?? "Send this clip to the bound model."}
          onClick={send}
        >
          {busy ? "Listening…" : "Transcribe"}
        </button>
      </div>
      {blocker && file && <p className="field-hint">{blocker}</p>}

      {refusal && <p className="result err">{refusal}</p>}
      {result && (
        <div className="try-result">
          <p className="try-answer">
            <span className="lbl">Transcript</span> {result.transcript || "(no speech heard)"}
          </p>
          <div className="stats">
            <div className="stat">
              <div className="lbl">Length</div>
              <div className="num small-num">{secondsText(result.seconds)}</div>
            </div>
            <div className="stat">
              <div className="lbl">Latency</div>
              <div className="num small-num">{result.latencyMs} ms</div>
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
