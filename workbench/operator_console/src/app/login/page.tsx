"use client";

import { useState } from "react";

// The INTERIM staff sign-in (D35.3). Posts the shared secret to the server,
// which validates it and sets an httpOnly cookie; the secret is never held in
// client state beyond this form. Replaced by the staff Entra directory when the
// owner stands it up.
export default function LoginPage() {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch("/api/operator/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ secret }),
    });
    setBusy(false);
    if (res.ok) {
      window.location.href = "/";
    } else {
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      setError(body.error ?? `Sign-in failed (${res.status})`);
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
