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
    <main className="wrap">
      <h1>Operator Console</h1>
      <p className="muted">Staff sign-in (interim). Platform staff only.</p>
      <form className="panel" onSubmit={submit} style={{ maxWidth: 380 }}>
        <label htmlFor="secret">Staff secret</label>
        <input
          id="secret"
          type="password"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
          autoComplete="off"
        />
        <button type="submit" disabled={busy || secret.length === 0}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {error && <div className="result err">{error}</div>}
      </form>
    </main>
  );
}
