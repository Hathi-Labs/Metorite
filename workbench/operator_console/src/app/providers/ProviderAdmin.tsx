"use client";

import { useState } from "react";

import {
  type ProviderCred,
  byokOrgs,
  coverageLine,
  describeScope,
  isLive,
  wouldRotate,
} from "@/lib/providers";

// The operator's provider-account surface — WS-31 CP-10 slice 4.
//
// ⚠️ **The secret lives in local state and nowhere else.** It is cleared the
// moment the Console answers, it is never put in a URL, never logged, and
// never read back — the Console's list query does not select the column it is
// stored in, so there is nothing to read back.
//
// ⚠️ **No GET runs from this component.** The page's server component reads
// the list with the caller's own token and passes it down. A client fetch
// would put the list on a path the browser can replay.

type Props = { creds: ProviderCred[] };

export default function ProviderAdmin({ creds }: Props) {
  const [provider, setProvider] = useState("");
  const [secret, setSecret] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [label, setLabel] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rotating = wouldRotate(creds, provider, orgSlug.trim() || null);
  const live = creds.filter(isLive);
  const byok = byokOrgs(creds);

  async function install(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const r = await fetch("/api/operator/providers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          provider: provider.trim(),
          secret,
          api_base: apiBase.trim() || null,
          label: label.trim() || null,
          org_slug: orgSlug.trim() || null,
        }),
      });
      const text = await r.text();
      if (!r.ok) {
        // The Console is the authority on a refusal — it knows the provider
        // shape rule, the whitespace rule and the length rule. Paraphrasing
        // would be a second vocabulary for one 400.
        setError(`The Console refused: ${text}`);
        return;
      }
      // ⚠️ Cleared on success, always. A key left in a field survives a tab
      // switch and a screen share.
      setSecret("");
      const body = JSON.parse(text) as { rotated?: boolean };
      setNote(
        body.rotated
          ? `Rotated. The previous ${provider.trim()} key is revoked and no longer used.`
          : `Installed. Reload to see it listed.`,
      );
    } catch {
      setError("The request did not complete. Nothing was installed.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(c: ProviderCred) {
    const what = c.org_slug ? `${c.provider} for ${c.org_slug}` : `${c.provider} (PLATFORM)`;
    const warn = c.org_slug
      ? `Revoke ${what}? That organization falls back to our platform account.`
      : `Revoke ${what}?\n\nThis stops every AI call that is not BYOK.`;
    if (!confirm(warn)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/operator/providers/revoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider: c.provider, org_slug: c.org_slug }),
      });
      const text = await r.text();
      if (!r.ok) setError(`The Console refused: ${text}`);
      else setNote(`Revoked ${what}. Reload to refresh the list.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className={live.length === 0 ? "banner" : "note"}>
        {coverageLine(creds)}
        {byok.length > 0 && ` BYOK organizations: ${byok.join(", ")}.`}
      </div>

      <h2>Install or rotate</h2>
      <p className="muted">
        Installing a provider we already hold a live key for <strong>rotates</strong>{" "}
        it: the old key is revoked and the new one installed together, so there
        is never a moment with two live keys or none.
      </p>

      <form onSubmit={install}>
        <label>
          Provider
          <input
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            placeholder="anthropic"
            required
          />
        </label>
        <label>
          Secret
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="the vendor API key"
            autoComplete="off"
            required
          />
        </label>
        <label>
          API base <span className="muted">(optional)</span>
          <input
            value={apiBase}
            onChange={(e) => setApiBase(e.target.value)}
            placeholder="https://api.anthropic.com"
          />
        </label>
        <label>
          Label <span className="muted">(optional)</span>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="what this account is, for the next person"
          />
        </label>
        <label>
          Organization slug <span className="muted">(optional — BYOK only)</span>
          <input
            value={orgSlug}
            onChange={(e) => setOrgSlug(e.target.value)}
            placeholder="leave empty for the platform account"
          />
        </label>

        {rotating && (
          <div className="banner">
            A live {provider.trim()} credential already exists for{" "}
            {orgSlug.trim() || "the platform account"}. Saving will REPLACE it.
          </div>
        )}

        <button type="submit" disabled={busy}>
          {rotating ? "Rotate" : "Install"}
        </button>
      </form>

      {error && <div className="banner">{error}</div>}
      {note && <div className="note">{note}</div>}

      <h2>Installed</h2>
      {creds.length === 0 ? (
        <p className="muted">Nothing installed yet.</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Scope</th>
                <th>Label</th>
                <th>API base</th>
                <th>Installed</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {creds.map((c) => (
                <tr key={c.id} className={isLive(c) ? undefined : "muted"}>
                  <td>{c.provider}</td>
                  <td>{describeScope(c)}</td>
                  <td>{c.label ?? "—"}</td>
                  <td>{c.api_base ?? "default"}</td>
                  <td>{c.created_at ?? "—"}</td>
                  <td>
                    {isLive(c) ? (
                      <button onClick={() => revoke(c)} disabled={busy}>
                        Revoke
                      </button>
                    ) : (
                      "revoked"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
