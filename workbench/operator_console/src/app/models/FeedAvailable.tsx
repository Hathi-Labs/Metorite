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
//
// 🔴 **PICK, then add — 2026-09-21.** One button per row is the right act for
// one model and the wrong one for eleven: the operator setting a company up
// wants nine specific models out of four vendors, and a per-row click makes
// that nine round trips they have to sit and watch. Worse, every model added
// by mistake was permanent until `RemoveModel` existed, so the cheap act had
// no cheap undo and people over-added to avoid coming back.
//
// ⚠️ **The selection spans vendors and survives the search box**, because the
// nine models are not all under one vendor and narrowing is how you find each
// one. It is cleared by the operator, or by a successful add — never by a
// re-render.
//
// ⚠️ **One request at a time, and it reports what LANDED**, the same rule
// `FillAllBlind` follows. A `Promise.all` over eleven writes hides which one
// failed and hands the Console eleven concurrent writes from one click.

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { categoricalChip, providerGlyph } from "@/lib/categorical";
import { KIND_LABEL, type FeedModel, type VendorFeed } from "@/lib/contract";
import { HELP_AVAILABLE } from "@/lib/help";
import { ADD, EDIT } from "@/lib/words";
import {
  availableByVendor,
  canFillFromFeed,
  declareBodies,
  feedPriceLabel,
  selectableIds,
  servableFeedModels,
  toggleAll,
  togglePick,
  without,
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
  // The id being written by a single-row click, or null.
  const [busy, setBusy] = useState<string | null>(null);
  // ⚠️ A separate flag, never a sentinel inside `busy`. A sentinel has to be
  // a string no model id can equal, and the obvious choices are a control
  // byte — which is how a NUL reached a source file here once already.
  const [bulkBusy, setBulkBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // ⚠️ The ids, not the rows. A `FeedModel` is re-derived on every render by
  // `availableByVendor`, so holding objects would compare by identity and a
  // tick would come undone the next time the operator typed a letter.
  const [picked, setPicked] = useState<Set<string>>(new Set());
  // Which vendors the reader asked to see in full. `PER_VENDOR_CAP` keeps the
  // panel readable; a vendor whose models you are picking from is exactly
  // where the cap stops being a kindness.
  const [showAll, setShowAll] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<{ ok: number; failed: string[] } | null>(null);

  const groups = useMemo(
    () => availableByVendor(feed, query),
    [feed, query],
  );

  if (feed.available.length === 0) return null;

  /** Declare ONE model and save its facts. Returns the problem, or null.
   *
   * ⚠️ **The single row and the bulk button share this**, so the two writes
   * cannot grow different rules about what a blank means or what a half-
   * failure says. It is the same two POSTs it always was. */
  async function addOne(f: FeedModel): Promise<string | null> {
    const bodies = declareBodies(f);
    if (!bodies.capability) return `${f.id} has no job the Router can serve.`;
    try {
      const cap = await fetch("/api/operator/catalog/capabilities", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(bodies.capability),
      });
      if (!cap.ok) return `The Console refused ${f.id}: ${await cap.text()}`;
      const prof = await fetch("/api/operator/catalog/profiles", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(bodies.profile),
      });
      if (!prof.ok) {
        // The capability landed, the facts did not — say exactly that.
        return (
          `${f.id} is declared, but its facts failed to save: ` +
          `${await prof.text()}. Use ${EDIT.open} on its card.`
        );
      }
      return null;
    } catch {
      return (
        `The Console did not answer while adding ${f.id} — check the ` +
        "network, then look at its card: the declare may have landed " +
        "without its facts."
      );
    }
  }

  async function add(f: FeedModel) {
    setBusy(f.id);
    setErr(null);
    setDone(null);
    const problem = await addOne(f);
    setBusy(null);
    if (problem) {
      setErr(problem);
      return;
    }
    // ⚠️ Untick it too. The row is about to leave the list — it is declared
    // now — and a selection holding an id that is no longer on offer would
    // put a phantom into the next "Add N selected".
    setPicked(without(picked, f.id));
    router.refresh();
  }

  /** Add everything ticked, one at a time, and say what landed. */
  async function addPicked() {
    const rows = servableFeedModels(feed).filter((f) => picked.has(f.id));
    if (rows.length === 0) return;
    setBulkBusy(true);
    setErr(null);
    setDone(null);
    let ok = 0;
    const failed: string[] = [];
    for (const f of rows) {
      const problem = await addOne(f);
      if (problem === null) ok++;
      else failed.push(f.id);
    }
    setBulkBusy(false);
    setDone({ ok, failed });
    // ⚠️ Keep the ones that FAILED ticked, and only those. The operator's next
    // act is to retry or read the refusal, and clearing the lot would hide
    // which models they still do not have.
    setPicked(new Set(failed));
    // Refresh even on a partial failure: the models that DID land are in the
    // catalog, and a list still offering them invites a second add.
    router.refresh();
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
          title={HELP_AVAILABLE.search}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {err && <p className="result err">{err}</p>}

      {/* 🔴 **What you picked, and the one button that writes it.** It is
          drawn only when something is ticked: a permanently-present bar
          reading "Add 0 selected" is a control that can only ever do nothing,
          and it would sit above the search box on every visit.

          ⚠️ **The count spans vendors.** That is the point — the nine models
          a company actually needs are rarely all under one key. */}
      {picked.size > 0 && (
        <div className="banner info pickbar" role="status">
          <strong>
            {picked.size} model{picked.size === 1 ? "" : "s"} picked
          </strong>
          <div className="rowline">
            <button
              type="button"
              className="primary"
              disabled={bulkBusy || busy !== null}
              title={HELP_AVAILABLE.addSelected}
              onClick={addPicked}
            >
              {bulkBusy ? ADD.busy : ADD.selected(picked.size)}
            </button>
            <button
              type="button"
              className="linklike"
              disabled={bulkBusy}
              title={HELP_AVAILABLE.clearPicks}
              onClick={() => setPicked(new Set())}
            >
              Clear picks
            </button>
          </div>
          <p className="muted small">
            Each one is declared and its vendor facts saved, one at a time.
            Nothing is charged to a customer until a tier points at it and the
            rate card prices it.
          </p>
        </div>
      )}

      {/* ⚠️ Survives the refresh, because the rows that succeeded have left
          the list and the reader needs to know they landed rather than
          vanished. */}
      {done && (
        <p className={done.failed.length === 0 ? "result ok" : "result err"}>
          {done.failed.length === 0
            ? `Added ${done.ok} to the catalog.`
            : `Added ${done.ok}. ${done.failed.length} refused — ${done.failed
                .slice(0, 3)
                .join(", ")}${done.failed.length > 3 ? "…" : ""}. Those are still picked, so you can try again.`}
        </p>
      )}

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
      {[...groups.entries()].map(([vendor, rows]) => {
        // ⚠️ The cap applies to what is DRAWN, and every "select all" below
        // acts on the drawn rows only. A tick can therefore never select a
        // model the operator has not seen.
        const visible = showAll.has(vendor) ? rows : rows.slice(0, PER_VENDOR_CAP);
        const ids = selectableIds(visible);
        const allOn = ids.length > 0 && ids.every((id) => picked.has(id));
        return (
        <details
          key={vendor}
          className="feedvendor"
          open={query.trim() !== "" || groups.size === 1}
        >
          <summary title={HELP_AVAILABLE.vendor}>
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
          <div className="tablewrap">
            {/* ⚠️ A wide table must scroll INSIDE its own box. Without this the
            table widens the document and the whole page scrolls
            sideways, which moves the nav and every other panel with
            it. Measured at 390px on 2026-09-20. */}
            <table>
              <thead>
                <tr>
                  <th className="tickcol">
                    {/* ⚠️ Labelled for a screen reader, never with visible
                        text. A word here would be the widest thing in a 28px
                        column and push the table sideways. */}
                    <input
                      type="checkbox"
                      checked={allOn}
                      disabled={ids.length === 0 || bulkBusy}
                      title={HELP_AVAILABLE.pickVendor}
                      aria-label={`Pick every ${vendor} model shown`}
                      onChange={() => setPicked(toggleAll(picked, ids))}
                    />
                  </th>
                  <th title={HELP_AVAILABLE.colModel}>Model</th>
                  <th title={HELP_AVAILABLE.colJob}>Job</th>
                  <th title={HELP_AVAILABLE.colContext}>Reads at most</th>
                  {/* ⚠️ Not "per 1M". A transcribe model is sold by the minute
                      and a speech model by the character — the unit belongs to
                      the row, and `feedPriceLabel` names it there. */}
                  <th title={HELP_AVAILABLE.colPrice}>We would pay</th>
                  <th aria-label="Add" />
                </tr>
              </thead>
              <tbody>
                {visible.map((f) => (
                  <tr key={f.id} className={picked.has(f.id) ? "picked" : undefined}>
                    <td className="tickcol">
                      {/* ⚠️ No tick on a row the Router cannot serve. The cell
                          stays, so the column does not jag. */}
                      {f.task && (
                        <input
                          type="checkbox"
                          checked={picked.has(f.id)}
                          disabled={bulkBusy}
                          title={HELP_AVAILABLE.pick}
                          aria-label={`Pick ${f.id}`}
                          onChange={() => setPicked(togglePick(picked, f.id))}
                        />
                      )}
                    </td>
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
                        // ⚠️ Link weight, not a filled button. The pick
                        // bar above carries the one primary act on this
                        // panel, and twelve solid buttons down a table
                        // compete with it and with each other.
                        <button
                          type="button"
                          className="linklike"
                          disabled={busy !== null || bulkBusy}
                          onClick={() => add(f)}
                          title={
                            canFillFromFeed(f)
                              ? HELP_AVAILABLE.add
                              : HELP_AVAILABLE.addUnpriced
                          }
                        >
                          {busy === f.id
                            ? ADD.busy
                            : canFillFromFeed(f)
                              ? ADD.one
                              : ADD.unservable}
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
          </div>
          {/* 🔴 The note used to say "search to narrow the rest" and stop
              there. An operator who wants the 40th model of 517 cannot search
              for a name they do not know yet, so the cap was a wall rather
              than a kindness. Now it is a door. */}
          {rows.length > PER_VENDOR_CAP && (
            <p className="note">
              {showAll.has(vendor) ? (
                <>
                  Showing all {rows.length}.{" "}
                  <button
                    type="button"
                    className="linklike"
                    onClick={() => setShowAll(without(showAll, vendor))}
                  >
                    Show the first {PER_VENDOR_CAP}
                  </button>
                </>
              ) : (
                <>
                  Showing {PER_VENDOR_CAP} of {rows.length}.{" "}
                  <button
                    type="button"
                    className="linklike"
                    title={HELP_AVAILABLE.showAll}
                    onClick={() => setShowAll(togglePick(showAll, vendor))}
                  >
                    Show all {rows.length}
                  </button>{" "}
                  <span className="muted small">— or search to narrow them.</span>
                </>
              )}
            </p>
          )}
        </details>
        );
      })}

      {groups.size === 0 && (
        <p className="muted">Nothing matches &ldquo;{query}&rdquo;.</p>
      )}
    </section>
  );
}
