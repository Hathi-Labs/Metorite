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
import { driftFor, feedById } from "@/lib/feed";
import {
  NO_FILTERS,
  STATUS_LABEL,
  type Filters,
  type SortKey,
  filterModels,
  formatTokens,
  formatVendorPrice,
  kindFacets,
  providerFacets,
  resultLine,
  sortModels,
  statusOf,
  toggle,
} from "@/lib/modelSearch";
import { chipClass, type Tone } from "@/lib/tone";
import FeedAvailable from "./FeedAvailable";
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

  const shown = useMemo(
    () => sortModels(filterModels(models, f, armed), sort),
    [models, f, armed, sort],
  );
  const kinds = useMemo(
    () => kindFacets(models, f, MODEL_KINDS, armed), [models, f, armed]);
  const providers = useMemo(
    () => providerFacets(models, f, armed), [models, f, armed]);
  const byId = useMemo(() => feedById(feed), [feed]);
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

  return (
    <>
      <FeedStrip feed={feed} />
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

      <div className="facets">
        <div className="facetrow">
          <span className="facetlabel">Can</span>
          {kinds.map((k) => (
            <button
              key={k.value}
              type="button"
              className="facet"
              aria-pressed={f.kinds.includes(k.value)}
              // ⚠️ Disabled at zero, not hidden. A chip that disappears makes
              // the row jump under the pointer and hides that the capability
              // exists at all.
              disabled={k.count === 0 && !f.kinds.includes(k.value)}
              onClick={() => setF({ ...f, kinds: toggle(f.kinds, k.value as ModelKind) })}
            >
              {KIND_LABEL[k.value]}
              <span className="count">{k.count}</span>
            </button>
          ))}
        </div>

        <div className="facetrow">
          <span className="facetlabel">From</span>
          {providers.map((p) => (
            <button
              key={p.value}
              type="button"
              className="facet"
              aria-pressed={f.providers.includes(p.value)}
              disabled={p.count === 0 && !f.providers.includes(p.value)}
              onClick={() => setF({ ...f, providers: toggle(f.providers, p.value) })}
            >
              <span className="glyph">{providerGlyph(p.value)}</span>
              {p.value}
              <span className="count">{p.count}</span>
            </button>
          ))}
        </div>

        <div className="facetrow">
          <span className="facetlabel">State</span>
          {(["costed", "costblind", "nokey", "undeclared"] as const).map((s) => (
            <button
              key={s}
              type="button"
              className="facet"
              aria-pressed={f.statuses.includes(s)}
              onClick={() => setF({ ...f, statuses: toggle(f.statuses, s) })}
            >
              {STATUS_LABEL[s]}
              <span className="count">
                {filterModels(models, { ...f, statuses: [s] }, armed).length}
              </span>
            </button>
          ))}
          {dirty && (
            <button type="button" className="linklike" onClick={() => setF(NO_FILTERS)}>
              Clear all
            </button>
          )}
        </div>
      </div>

      <p className="resultline">{resultLine(shown.length, models.length, f)}</p>

      <div className="modelgrid">
        {shown.map((m) => (
          <Card key={m.id} m={m} f={byId.get(m.id)} armed={armed} />
        ))}
      </div>

      <FeedAvailable feed={feed} />
    </>
  );
}
