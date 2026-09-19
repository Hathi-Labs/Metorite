"use client";

// The model catalog, as something you can actually search.
//
// 🔴 **What changed.** The old page drew three stacked tables — capabilities,
// bindings, rates — and asked the reader to join them by eye. That was
// survivable with one provider. OpenRouter alone exposes two hundred models,
// and an operator asked "which of these can read an image" had nowhere to ask
// it. Tiers moved to their own page, so this one has a single job: find a
// model and know whether we can sell it.
//
// ⚠️ Every judgement is imported from `@/lib/modelSearch`, never written
// inline. This app's suite carries no React renderer, so logic in JSX is
// untested by construction and `modelSearch.test.ts` is the fence.
//
// ⚠️ **The filter chips carry counts, and the counts are computed against the
// OTHER filters.** A chip that says 14 and returns nothing is worse than a chip
// with no number on it at all.

import { useMemo, useState } from "react";

import { categoricalChip, providerGlyph } from "@/lib/categorical";
import {
  KIND_LABEL,
  MODEL_KINDS,
  type CatalogModel,
  type FeedModel,
  type ModelKind,
  type VendorFeed,
} from "@/lib/contract";
import { blindButFillable, driftFor, feedById } from "@/lib/feed";
import {
  NO_FILTERS,
  STATUS_LABEL,
  type Filters,
  type SortKey,
  ATTENTION_STATUSES,
  attentionCount,
  filterModels,
  formatTokens,
  formatVendorPrice,
  kindFacets,
  pageOf,
  usefulKindFacets,
  resultLine,
  sortModels,
  statusOf,
  toggle,
} from "@/lib/modelSearch";
import { chipClass, type Tone } from "@/lib/tone";
import FeedAvailable from "./FeedAvailable";
import FillAllBlind from "./FillAllBlind";
import FeedStrip from "./FeedStrip";
import ModelDetails from "./ModelDetails";

const STATUS_TONE: Record<ReturnType<typeof statusOf>, Tone> = {
  costed: "ok",
  undeclared: "danger",
  // A vendor we hold no live key for: every call to this model fails. The
  // seed proved the state real - it ships tier-stt on a groq model no
  // fresh install has a key for (owner report, 2026-08-30).
  nokey: "danger",
  // Costs-blind serves fine — but every margin that touches it reads as
  // unknown, so it warns until the vendor price is recorded (or fetched).
  costblind: "warn",
};

const SORTS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "provider", label: "Provider" },
  { key: "context", label: "Biggest window" },
  { key: "cheapest", label: "Cheapest" },
];

function Card({
  m, f, armed,
}: {
  m: CatalogModel; f: FeedModel | undefined; armed: string[];
}) {
  const status = statusOf(m, armed);
  // The vendor moved a price under a typed profile (014). The chip is the
  // ALERT; the numbers and the copy button live in "Edit details".
  const drift = driftFor(m, f);
  return (
    <article className="modelcard">
      <header>
        <div>
          <h3>{m.label}</h3>
          <span className="mono small muted">{m.id}</span>
        </div>
        <span className={chipClass(STATUS_TONE[status])}>
          {STATUS_LABEL[status]}
        </span>
      </header>

      {drift.length > 0 && (
        <p className="chip warn" title={drift
          .map((d) => `${d.label}: we say $${d.ours}, the vendor says $${d.upstream}`)
          .join(" · ")}>
          the vendor moved {drift.length === 1 ? "a price" : `${drift.length} prices`}
        </p>
      )}

      {m.description && <p className="modeldesc">{m.description}</p>}

      <div className="kindrow">
        <span className={categoricalChip(m.provider)} title={`Supplied by ${m.provider}`}>
          <span className="glyph">{providerGlyph(m.provider)}</span>
          {m.provider}
        </span>
        {m.kinds.map((k) => (
          <span key={k} className="chip">
            {KIND_LABEL[k]}
          </span>
        ))}
        {m.kinds.length === 0 && (
          <span className="chip warn" title="No task is declared for this model">
            nothing declared
          </span>
        )}
      </div>

      <dl className="modelfacts">
        <div>
          <dt>Reads at most</dt>
          <dd>{formatTokens(m.contextWindow)}</dd>
        </div>
        <div>
          <dt>Writes at most</dt>
          <dd>{formatTokens(m.maxOutput)}</dd>
        </div>
        <div>
          {/* ⚠️ "We pay" is not decoration. This is the VENDOR's price, and
              the rate card is what we charge — two numbers on two tables, and
              reading one as the other inverts a margin. */}
          <dt>We pay, per 1M</dt>
          <dd>{formatVendorPrice(m.inputPer1M, m.outputPer1M)}</dd>
        </div>
      </dl>

      <ModelDetails m={m} feedRow={f} />
    </article>
  );
}

export default function ModelBrowser({
  models,
  feed,
  armed,
}: {
  models: CatalogModel[];
  feed: VendorFeed;
  /** Providers with a live platform key — decides the `nokey` state. */
  armed: string[];
}) {
  const [f, setF] = useState<Filters>(NO_FILTERS);
  const [sort, setSort] = useState<SortKey>("name");
  // ⚠️ **Keyed on the FILTERS, not a bare boolean.** An operator who narrows
  // the list is asking a new question, and carrying "show everything" into it
  // re-renders the wall they just escaped. Holding the key the expansion was
  // granted for collapses it on any filter change, with no effect to forget.
  const [expandedFor, setExpandedFor] = useState<string | null>(null);

  const shown = useMemo(
    () => sortModels(filterModels(models, f, armed), sort),
    [models, f, armed, sort],
  );
  const kinds = useMemo(
    () => kindFacets(models, f, MODEL_KINDS, armed), [models, f, armed]);
  const byId = useMemo(() => feedById(feed), [feed]);
  // ⚠️ Only the capability chips with models behind them, and NONE at all when
  // one kind is left: filtering a list to its only kind returns the same list.
  const liveKinds = useMemo(
    () => usefulKindFacets(kinds, f.kinds),
    [kinds, f.kinds],
  );
  const attention = useMemo(
    () => attentionCount(models, f, armed),
    [models, f, armed],
  );
  const filterKey = JSON.stringify(f);
  const page = useMemo(
    () => pageOf(shown, expandedFor === filterKey),
    [shown, expandedFor, filterKey],
  );
  const dirty =
    f.query.trim() !== "" || f.kinds.length + f.providers.length + f.statuses.length > 0;

  if (models.length === 0) {
    // ⚠️ The feed pieces still render — a fresh install with keys but no
    // declarations is EXACTLY when "available from your vendors" earns its
    // keep: the first declare should be a click, not a form.
    return (
      <>
        <FeedStrip feed={feed} />
        <div className="empty">
          <h2>No models yet</h2>
          <p className="muted">
            Nothing has been declared, so no tier can point at anything and
            every AI request fails. Add one from your vendors below, or
            declare one by hand.
          </p>
        </div>
        <FeedAvailable feed={feed} />
      </>
    );
  }

  // 🔴 **The feed list goes ABOVE the catalog until something is costed.**
  // "Available from your vendors" used to start 5474px down a 13257px page,
  // under four and a half screens of cards — so the one-click path was
  // unreachable in the exact state it exists for.
  //
  // ⚠️ **Ordered on "nothing is costed yet", NOT on "anything is blind".** The
  // second condition flips while you work: filling the last blind model would
  // rearrange the page under the cursor at the moment of the click. This one
  // changes once, when the first model gets a price.
  const nothingCosted = models.every((m) => statusOf(m, armed) !== "costed");
  const blind = blindButFillable(models, feed, armed);

  return (
    <>
      <FeedStrip feed={feed} />
      <FillAllBlind blind={blind} feed={feed} />
      {nothingCosted && <FeedAvailable feed={feed} />}
      <div className="toolbar">
        <input
          className="search"
          type="search"
          placeholder="Search by name, provider, or what it is good at…"
          aria-label="Search models"
          value={f.query}
          onChange={(e) => setF({ ...f, query: e.target.value })}
        />
        <label className="sortpick">
          <span className="muted small">Sort</span>
          <select
            aria-label="Sort models"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
          >
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* 🔴 **THREE ROWS OF PILLS BECAME ONE.** The page carried "Can", "From"
          and "State" — about forty-five controls above the first model — and
          the owner could not tell what any of the three asked. Measured
          2026-09-19: of seven capability chips only two ever had models, and
          the thirty vendor chips each returned one or two rows out of
          forty-three. A filter whose every option returns two of forty-three
          is a list wearing a filter's clothes.

          What replaced them:
            · capability  — typed. `matchesQuery` reads the kind labels now.
            · vendor      — typed. It always matched `m.provider`.
            · state       — ONE toggle. Three of the four chips asked the same
                            question, and the fourth asked for the models with
                            no problem, which is what the page already shows.

          The capability row survives ONLY where it earns its place: more than
          one kind with models behind it. */}
      <div className="facets">
        {/* ⚠️ **No row LABEL.** "Can", "From" and "State" were the three words
            the owner could not read, and a chip reading "Speech to text" says
            what it is without one. A label earns its place when the options
            are ambiguous alone; these are not. */}
        {liveKinds.length > 0 && (
          <div className="facetrow">
            {liveKinds.map((k) => (
              <button
                key={k.value}
                type="button"
                className="facet"
                aria-pressed={f.kinds.includes(k.value)}
                onClick={() => setF({ ...f, kinds: toggle(f.kinds, k.value) })}
              >
                {KIND_LABEL[k.value]}
                <span className="count">{k.count}</span>
              </button>
            ))}
          </div>
        )}

        <div className="facetrow">
          {/* ⚠️ Drawn only when there IS something to attend to. A toggle
              reading "0 need attention" is a control that can only ever
              return an empty list. */}
          {attention > 0 && (
            <button
              type="button"
              className="facet"
              aria-pressed={f.statuses.length > 0}
              onClick={() =>
                setF({
                  ...f,
                  statuses: f.statuses.length > 0 ? [] : ATTENTION_STATUSES,
                })
              }
            >
              Needs attention
              <span className="count">{attention}</span>
            </button>
          )}
          {dirty && (
            <button type="button" className="linklike" onClick={() => setF(NO_FILTERS)}>
              Clear
            </button>
          )}
          <span className="muted small">
            Search matches the name, the vendor, what it is good at, and what it
            can do — try &ldquo;deepseek&rdquo; or &ldquo;reads images&rdquo;.
          </span>
        </div>
      </div>

      <p className="resultline">{resultLine(shown.length, models.length, f)}</p>

      <div className="modelgrid">
        {page.shown.map((m) => (
          <Card key={m.id} m={m} f={byId.get(m.id)} armed={armed} />
        ))}
      </div>

      {/* 🔴 Every card builds a feed lookup and a drift comparison. Rendering
          two hundred of them before anybody sees the first is why this page
          measured 13483px on a catalog of forty-three. */}
      {page.hidden > 0 && (
        <p className="resultline">
          <button type="button" className="linklike" onClick={() => setExpandedFor(filterKey)}>
            Show {page.hidden} more
          </button>{" "}
          <span className="muted small">
            — or narrow the list with the search box and the filters above.
          </span>
        </p>
      )}

      {/* ⚠️ Rendered here ONLY when it was not drawn above. Two copies would
          be two sets of "Add" buttons for the same rows. */}
      {!nothingCosted && <FeedAvailable feed={feed} />}
    </>
  );
}
