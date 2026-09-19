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
import {
  availableByVendor,
  canFillFromFeed,
  declareBodies,
  feedPriceLabel,
} from "@/lib/feed";

/** How many rows to draw per vendor before pointing at the search box.
 *
 * ⚠️ **Was 40, lowered 2026-09-19.** Forty reads fine with one vendor and
 * badly with three: 517 models across three keys drew 120 rows and made this
 * panel the largest thing on a page that already measured 13483px. The search
 * box above narrows within a vendor, and the note under each table says what
 * is held back — so a lower cap costs reach nothing and buys the whole panel
 * back onto a screen. */
const PER_VENDOR_CAP = 12;

/** How many of these the feed cannot price AT ALL.
 *
 * 🔴 **Adding one lands a model that is COSTS BLIND.** The Add button looked
 * identical whether the feed knew a price or not, so the click quietly created
 * the exact state the page above it nags about.
 *
 * ⚠️ **NOT the models showing a dash in the price column — that was my first
 * reading and it was wrong.** `groq/whisper-large-v3` carries a per-SECOND
 * rate and `groq/canopylabs/orpheus-v1-english` a per-CHARACTER one. Both are
 * priced; the column simply read token rates and nothing else.
 * `feedPriceLabel` shows them now, and this count is the genuinely unpriced
 * tail — the same judgement `canFillFromFeed` makes, so the two cannot
 * disagree. */
function unpricedCount(rows: FeedModel[]): number {
  return rows.filter((f) => !canFillFromFeed(f)).length;
}

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
    // ⚠️ `id` is the jump target the feed strip links to. This panel can sit
    // thousands of pixels down, and without an anchor the only way to it is
    // scrolling past the whole declared catalog.
    <section className="panel" id="available">
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

      {/* 🔴 **ONE LINE PER VENDOR, CLOSED.** Four vendors drew four flat
          tables and about fifty rows before anybody had chosen a vendor —
          a wall, and the owner said so. Closed, this panel is four lines:
          pick a vendor, then look at its models.

          ⚠️ **Native `details`, not a state toggle.** It is keyboard and
          screen-reader correct with no work, and it survives a re-render. The
          `open` prop only OVERRIDES the default — a vendor the reader opened
          by hand stays open until the search changes.

          ⚠️ **A search OPENS every vendor that matched.** A closed accordion
          hiding the thing you just searched for is the worst of both designs.
          One vendor opens too, because a single closed row is a click that
          could only ever have one outcome. */}
      {[...groups.entries()].map(([vendor, rows]) => (
        <details
          key={vendor}
          className="feedvendor"
          open={query.trim() !== "" || groups.size === 1}
        >
          <summary>
            <span className={categoricalChip(vendor)}>
              <span className="glyph">{providerGlyph(vendor)}</span>
              {vendor}
            </span>
            <span className="muted small">
              {rows.length} model{rows.length === 1 ? "" : "s"}
              {unpricedCount(rows) > 0 && (
                <> · {unpricedCount(rows)} with no price</>
              )}
            </span>
          </summary>
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Job</th>
                <th>Reads at most</th>
                {/* ⚠️ Not "per 1M". A transcribe model is sold by the minute
                    and a speech model by the character — the unit belongs to
                    the row, and `feedPriceLabel` names it there. */}
                <th>We would pay</th>
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
                    {/* ⚠️ Says WHY the dash is there. A bare "—" beside an Add
                        button that behaves identically taught nothing — the
                        model lands costs-blind and the reader finds out on the
                        page above. */}
                    {feedPriceLabel(f) !== null ? (
                      <span className="mono small">{feedPriceLabel(f)}</span>
                    ) : (
                      <span
                        className="chip warn"
                        title="The feed carries no price for this model. Adding it lands a costs-blind model, and its margin reads as unknown until somebody records a price by hand."
                      >
                        no price upstream
                      </span>
                    )}
                  </td>
                  <td>
                    {f.task ? (
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => add(f)}
                        title={
                          canFillFromFeed(f)
                            ? undefined
                            : "Adds the model, but it will be costs blind — the feed has no price for it"
                        }
                      >
                        {busy === f.id
                          ? "Adding…"
                          : canFillFromFeed(f)
                            ? "+ Add"
                            : "+ Add anyway"}
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
        </details>
      ))}

      {groups.size === 0 && (
        <p className="muted">Nothing matches &ldquo;{query}&rdquo;.</p>
      )}
    </section>
  );
}
