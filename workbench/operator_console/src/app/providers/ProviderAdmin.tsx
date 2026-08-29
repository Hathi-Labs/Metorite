"use client";

// Our provider accounts — WS-31 CP-10 slice 4, rebuilt as cards.
//
// 🔴 **What changed.** This was a flat table with one row per credential, which
// drew two keys for the same vendor as unrelated lines. A second platform key
// is exactly how a rate limit stops being an outage, and the table made that
// invisible. One card per vendor now, with every key it holds.
//
// ⚠️ **The secret lives in local state and nowhere else.** It is cleared the
// moment the Console answers, it is never put in a URL, never logged, and
// never read back — the Console's list query does not select the column it is
// stored in, so there is nothing to read back.
//
// ⚠️ **No GET runs from this component.** The page's server component reads
// the list with the caller's own token and passes it down. A client fetch
// would put the list on a path the browser can replay.

import { useState } from "react";

import { categoricalChip, providerGlyph } from "@/lib/categorical";
import { KNOWN_PROVIDERS, guideFor } from "@/lib/providerGuides";
import {
  type ProviderAccount,
  byokOrgs,
  coverageLine,
  describeScope,
  groupByProvider,
  groupLine,
  healthLabel,
  healthTone,
  isLive,
  wouldRotate,
} from "@/lib/providers";
import { chipClass } from "@/lib/tone";

type Props = { creds: ProviderAccount[] };

export default function ProviderAdmin({ creds }: Props) {
  const [provider, setProvider] = useState("");
  const [secret, setSecret] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [label, setLabel] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const rotating = wouldRotate(creds, provider, orgSlug.trim() || null);
  const live = creds.filter(isLive);
  const byok = byokOrgs(creds);
  const groups = groupByProvider(creds);
  const guide = guideFor(provider);

  function startAdd(preset: string) {
    setProvider(preset);
    setSecret("");
    setOrgSlug("");
    setLabel("");
    setApiBase("");
    setAdding(true);
    setNote(null);
    setError(null);
  }

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
          : "Installed. Reload to see it listed.",
      );
    } catch {
      setError("The request did not complete. Nothing was installed.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(c: ProviderAccount) {
    const what = c.orgSlug ? `${c.provider} for ${c.orgSlug}` : `${c.provider} (PLATFORM)`;
    const warn = c.orgSlug
      ? `Revoke ${what}? That organization falls back to our platform account.`
      : `Revoke ${what}?\n\nThis stops every AI call that is not BYOK.`;
    if (!confirm(warn)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await fetch("/api/operator/providers/revoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ provider: c.provider, org_slug: c.orgSlug }),
      });
      const text = await r.text();
      if (!r.ok) setError(`The Console refused: ${text}`);
      else setNote(`Revoked ${what}. Reload to refresh the list.`);
    } finally {
      setBusy(false);
    }
  }

  function Key({ c }: { c: ProviderAccount }) {
    return (
      <div className={`keyrow ${isLive(c) ? "" : "dead"}`}>
        <div className="keymain">
          <span className="keylabel">{c.label ?? describeScope(c)}</span>
          <span className="muted small">
            {describeScope(c)}
            {c.apiBase ? ` · ${c.apiBase}` : ""}
            {c.createdAt ? ` · added ${c.createdAt.slice(0, 10)}` : ""}
          </span>
          {c.healthNote && <span className="muted small">{c.healthNote}</span>}
        </div>
        {isLive(c) ? (
          <>
            <span className={chipClass(healthTone(c.health))}>
              {healthLabel(c.health)}
            </span>
            <button
              type="button"
              className="linklike"
              onClick={() => revoke(c)}
              disabled={busy}
            >
              Revoke
            </button>
          </>
        ) : (
          <span className="chip">revoked</span>
        )}
      </div>
    );
  }

  return (
    <>
      <div className={live.length === 0 ? "banner danger" : "note"}>
        {coverageLine(creds)}
        {byok.length > 0 && ` Organizations with their own key: ${byok.join(", ")}.`}
      </div>

      {error && <div className="banner danger">{error}</div>}
      {note && <div className="note">{note}</div>}

      {/* ── What we hold, one card per vendor ── */}
      {groups.length === 0 ? (
        <div className="empty">
          <h2>No vendor accounts</h2>
          <p className="muted">
            Every AI request fails until one is installed. Pick a vendor below
            and paste its key.
          </p>
        </div>
      ) : (
        <div className="provider-grid">
          {groups.map((g) => (
            <section className="provider-card" key={g.provider}>
              <header>
                <span className={categoricalChip(g.provider)}>
                  <span className="glyph">{providerGlyph(g.provider)}</span>
                  {g.provider}
                </span>
                <span className="muted small">{groupLine(g)}</span>
              </header>

              {guideFor(g.provider)?.description && (
                <p className="muted small">{guideFor(g.provider)?.description}</p>
              )}

              {[...g.platform, ...g.byok, ...g.revoked].map((c) => (
                <Key key={c.id} c={c} />
              ))}

              <button
                type="button"
                className="linklike add-job"
                onClick={() => startAdd(g.provider)}
              >
                + Add another {g.provider} key
              </button>
            </section>
          ))}
        </div>
      )}

      {/* ── Install or rotate ── */}
      <section className="panel">
        <div className="panel-head">
          <h2>{adding && provider ? `Add a ${provider} key` : "Add a vendor"}</h2>
          <p>
            Installing a vendor we already hold a live key for{" "}
            <strong>rotates</strong> it: the old key is revoked and the new one
            installed together, so there is never a moment with two live keys or
            none.
          </p>
        </div>

        <div className="facetrow">
          {KNOWN_PROVIDERS.map((p) => (
            <button
              key={p}
              type="button"
              className="facet"
              aria-pressed={provider === p}
              onClick={() => startAdd(p)}
            >
              <span className="glyph">{providerGlyph(p)}</span>
              {p}
            </button>
          ))}
          <button
            type="button"
            className="facet"
            aria-pressed={adding && !KNOWN_PROVIDERS.includes(provider)}
            onClick={() => startAdd("")}
          >
            Something else
          </button>
        </div>

        {adding && (
          <form onSubmit={install}>
            {guide && (
              <div className="guide">
                <p>{guide.description}</p>
                <ol>
                  {guide.steps.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ol>
                <p className="muted small">
                  {guide.keyLooksLike && (
                    <>
                      The key looks like{" "}
                      <span className="mono">{guide.keyLooksLike}</span> ·{" "}
                    </>
                  )}
                  <a href={guide.setupUrl} target="_blank" rel="noreferrer">
                    Open the vendor page
                  </a>
                </p>
              </div>
            )}

            <label>
              Vendor
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

            <details className="advanced">
              <summary>Where it goes, and who it is for</summary>
              <label>
                Label <span className="muted">(optional)</span>
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder="what this account is, for the next person"
                />
              </label>
              <label>
                API base <span className="muted">(optional)</span>
                <input
                  value={apiBase}
                  onChange={(e) => setApiBase(e.target.value)}
                  placeholder="leave empty for the vendor's own host"
                />
              </label>
              <label>
                One organization only <span className="muted">(optional)</span>
                <input
                  value={orgSlug}
                  onChange={(e) => setOrgSlug(e.target.value)}
                  placeholder="leave empty to serve every customer"
                />
              </label>
            </details>

            {rotating && (
              <div className="banner">
                A live {provider.trim()} key already exists for{" "}
                {orgSlug.trim() || "every customer"}. Saving will REPLACE it.
              </div>
            )}

            <div className="job-actions">
              <button type="submit" disabled={busy}>
                {rotating ? "Rotate" : "Install"}
              </button>
              <button
                type="button"
                className="linklike"
                onClick={() => setAdding(false)}
              >
                Cancel
              </button>
            </div>
          </form>
        )}
      </section>
    </>
  );
}
