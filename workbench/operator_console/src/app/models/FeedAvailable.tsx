"use client";

// "Available from your vendors" — what a CONNECTED vendor offers that nobody
// has declared. WS-31, migration 014.
//
// 🔴 **Adding a model here is the same two writes it always was** — a
// capability POST and a profile POST through the existing BFF routes — with
// every box filled from upstream instead of typed. The feed never writes by
// itself; the operator's click is the write. `feed.test.ts` fences the
// no-auto-save rule.
//
// ⚠️ Vendors we hold no live platform key for are not in this list — the
// Console excludes them. A model we cannot call is a brochure, not an offer.

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { categoricalChip, providerGlyph } from "@/lib/categorical";
import { KIND_LABEL, type FeedModel, type VendorFeed } from "@/lib/contract";
import { availableByVendor, declareBodies } from "@/lib/feed";
import { formatVendorPrice } from "@/lib/modelSearch";

const PER_VENDOR_CAP = 40;

/** The task in operator words; litellm's word when we cannot serve it. */
function jobWord(f: FeedModel): string {
  if (f.task && f.task in KIND_LABEL) {
    return KIND_LABEL[f.task as keyof typeof KIND_LABEL];
  }
  return f.mode;
}

export default function FeedAvailable({ feed }: { feed: VendorFeed }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const groups = useMemo(
    () => availableByVendor(feed, query),
    [feed, query],
  );

  if (feed.available.length === 0) return null;

  async function add(f: FeedModel) {
    const bodies = declareBodies(f);
    if (!bodies.capability) return;
    setBusy(f.id);
    setErr(null);
    try {
      const cap = await fetch("/api/operator/catalog/capabilities", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(bodies.capability),
      });
      if (!cap.ok) {
        setErr(`The Console refused ${f.id}: ${await cap.text()}`);
        return;
      }
      const prof = await fetch("/api/operator/catalog/profiles", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(bodies.profile),
      });
      if (!prof.ok) {
        // The capability landed, the facts did not — say exactly that.
        setErr(
          `${f.id} is declared, but its facts failed to save: ` +
            `${await prof.text()}. Use "Edit details" on its card.`,
        );
        return;
      }
      router.refresh();
    } catch {
      setErr(
        `The Console did not answer while adding ${f.id} — check the ` +
        "network, then look at its card: the declare may have landed " +
        "without its facts.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Available from your vendors</h2>
        <p>
          Models your connected vendors offer that are not declared here yet —
          with the window and the vendor&apos;s price already filled from
          upstream. Adding one declares it and saves those facts. It sells
          nothing until a tier points at it and the rate card prices it.
        </p>
      </div>

      <div className="toolbar">
        <input
          className="search"
          type="search"
          placeholder="Narrow by name or job — whisper, embedding, r1…"
          aria-label="Search available models"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {err && <p className="result err">{err}</p>}

      {[...groups.entries()].map(([vendor, rows]) => (
        <div key={vendor} className="feedvendor">
          <h3>
            <span className={categoricalChip(vendor)}>
              <span className="glyph">{providerGlyph(vendor)}</span>
              {vendor}
            </span>
            <span className="muted small">
              {rows.length} model{rows.length === 1 ? "" : "s"}
            </span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Job</th>
                <th>Reads at most</th>
                <th>We would pay, per 1M</th>
                <th aria-label="Add" />
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, PER_VENDOR_CAP).map((f) => (
                <tr key={f.id}>
                  <td>
                    <span className="mono small">{f.id}</span>
                    {f.deprecatedOn && (
                      <span
                        className="chip warn"
                        title="The vendor has announced a retirement date"
                      >
                        retires {f.deprecatedOn}
                      </span>
                    )}
                  </td>
                  <td>{jobWord(f)}</td>
                  <td>
                    {f.contextWindow === null
                      ? "—"
                      : f.contextWindow.toLocaleString("en-US")}
                  </td>
                  <td>
                    {formatVendorPrice(
                      f.inputPer1M === null ? null : Number(f.inputPer1M),
                      f.outputPer1M === null ? null : Number(f.outputPer1M),
                    )}
                  </td>
                  <td>
                    {f.task ? (
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => add(f)}
                      >
                        {busy === f.id ? "Adding…" : "+ Add"}
                      </button>
                    ) : (
                      <span
                        className="muted small"
                        title={`litellm calls this mode "${f.mode}" and the Router has no verb for it yet`}
                      >
                        not servable yet
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > PER_VENDOR_CAP && (
            <p className="note">
              Showing {PER_VENDOR_CAP} of {rows.length} — search to narrow the
              rest.
            </p>
          )}
        </div>
      ))}

      {groups.size === 0 && (
        <p className="muted">Nothing matches &ldquo;{query}&rdquo;.</p>
      )}
    </section>
  );
}
