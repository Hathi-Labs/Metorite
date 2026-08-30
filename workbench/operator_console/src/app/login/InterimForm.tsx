"use client";

import { useState } from "react";

// The INTERIM staff sign-in. Posts the shared secret to the server, which
// validates it and sets an httpOnly cookie.
//
// ⚠️ **This is F1, F2, F5 and F6 all at once, and it is still what runs.** One
// passphrase admits everybody, the cookie holds the passphrase itself, removing
// one person means changing the secret for the whole team, and nothing slows a
// guess. It stays only until the owner finishes H-54 and flips
// `OPERATOR_IDENTITY_ENABLED`. Deleting it before that would lock the team out
// of a live console — the order is in H-56.

export default function InterimForm() {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/operator/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ secret }),
      });
      if (res.ok) {
        window.location.href = "/";
        return;
      }
      const body = (await res.json().catch(() => ({}))) as {
        error?: string;
        detail?: string;
      };
      setError(body.error ?? body.detail ?? `Sign-in failed (${res.status})`);
    } catch {
      setError("No answer from the server — check the network and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-center">
      <form className="panel login-card" onSubmit={submit}>
        <h1 style={{ marginBottom: 4 }}>
          Metorite <span className="muted">Operator Console</span>
        </h1>
        <p className="muted">
          Customer management for platform staff. Not for customers — they sign
          in at app.metorite.com.
        </p>
        <label htmlFor="secret">Staff passphrase</label>
        <input
          id="secret"
          type="password"
          value={secret}
          autoFocus
          onChange={(e) => setSecret(e.target.value)}
          autoComplete="off"
        />
        <div className="field-hint">
          Don&apos;t have it? Ask the platform owner.
        </div>
        <button type="submit" disabled={busy || secret.length === 0}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {error && <div className="result err">{error}</div>}
      </form>
    </main>
  );
}
