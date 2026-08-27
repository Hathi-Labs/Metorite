"use client";

// The elevation window control (CP-12g, closes H-67).
//
// ⚠️ **CP-12g slice 1 shipped `/api/operator/elevate` and NOTHING CALLED IT.**
// The Console's §5 matrix binds NINE actions to a live window AND the `admin`
// role — `POST /orgs/purge`, `/keys`, `/keys/revoke`, `/discounts`,
// `/orgs/lifecycle`, `/providers/credentials` (+ revoke) and, as of CP-10
// slice 3, `/catalog/bindings` and `/catalog/rates`. Without a surface to open
// a window, the first signed-in operator could READ everything and change
// almost nothing.
//
// ⚠️ **It does not bite yet, and that is why it was easy to miss.** The interim
// path calls the Console with the shared `breakglass` token, which bypasses
// the matrix and the window, so all nine work today. They begin answering 403
// the moment somebody flips `OPERATOR_IDENTITY_ENABLED`.
//
// ⚠️ **This component renders NOTHING on the break-glass path**, and that is
// deliberate rather than a gap. `usesSessions()` reads server env, so a client
// component cannot see the mode — the sanctioned answer (H-67) is to accept
// the Console's own answer. `GET /operators/elevate` returns
// `{elevated:false}` for break-glass and 403 for nobody, and neither is a
// state worth a button that cannot work.

import { useCallback, useEffect, useState } from "react";

import {
  MIN_REASON,
  type ElevationWindow,
  reasonIsUsable,
  remaining,
} from "@/lib/elevation";

export default function Elevation() {
  const [win, setWin] = useState<ElevationWindow | null>(null);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [reference, setReference] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/operator/elevate");
      if (!res.ok) {
        // 403 is the break-glass path or a signed-out browser. Render nothing
        // rather than a control that cannot work.
        setWin(null);
        return;
      }
      setWin((await res.json()) as ElevationWindow);
    } catch {
      setWin(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // A window whose end nobody can see is one people re-open out of habit, so
  // the countdown ticks rather than showing a timestamp.
  useEffect(() => {
    if (!win?.elevated) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [win?.elevated]);

  const left = remaining(win?.expires_at, now);

  // Expired on the client. Re-read rather than guessing: the Console decides
  // when a window is over, and a clock skew must not leave a dead countdown
  // on screen claiming a privilege the operator no longer has.
  useEffect(() => {
    if (win?.elevated && left === null) void load();
  }, [win?.elevated, left, load]);

  async function elevate() {
    setError(null);
    if (!reasonIsUsable(reason)) {
      setError(
        `A reason of at least ${MIN_REASON} characters is required — it is ` +
          "what makes the audit row answer *why* afterwards.",
      );
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/operator/elevate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          reason: reason.trim(),
          reference: reference.trim() || null,
        }),
      });
      if (!res.ok) {
        // Relayed VERBATIM. The Console is the authority on a refusal, and
        // paraphrasing it here would invent a second vocabulary.
        setError(await res.text());
        return;
      }
      setOpen(false);
      setReason("");
      setReference("");
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function close() {
    setBusy(true);
    try {
      await fetch("/api/operator/elevate", { method: "DELETE" });
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (win === null) return null;

  if (win.elevated) {
    return (
      <span className="elevation elevated">
        <span title={win.reason}>Elevated{left ? ` · ${left}` : ""}</span>
        <button type="button" className="linklike" onClick={close} disabled={busy}>
          End now
        </button>
      </span>
    );
  }

  return (
    <span className="elevation">
      {open ? (
        <>
          <input
            aria-label="Reason for elevating"
            placeholder="Why (at least 12 characters)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <input
            aria-label="Reference"
            placeholder="Reference (optional)"
            value={reference}
            onChange={(e) => setReference(e.target.value)}
          />
          <button type="button" className="linklike" onClick={elevate} disabled={busy}>
            Confirm
          </button>
          <button
            type="button"
            className="linklike"
            onClick={() => {
              setOpen(false);
              setError(null);
            }}
          >
            Cancel
          </button>
          {error ? <span className="elevation-error">{error}</span> : null}
        </>
      ) : (
        <button type="button" className="linklike" onClick={() => setOpen(true)}>
          Elevate
        </button>
      )}
    </span>
  );
}
