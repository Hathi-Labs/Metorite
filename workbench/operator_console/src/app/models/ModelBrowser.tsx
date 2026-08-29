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
  type ModelKind,
} from "@/lib/contract";
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

const STATUS_TONE: Record<ReturnType<typeof statusOf>, Tone> = {
  ready: "ok",
  undeclared: "danger",
  unpriced: "warn",
};

const SORTS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Name" },
  { key: "provider", label: "Provider" },
  { key: "context", label: "Biggest window" },
  { key: "cheapest", label: "Cheapest" },
];

function Card({ m }: { m: CatalogModel }) {
  const status = statusOf(m);
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
    </article>
  );
}

export default function ModelBrowser({ models }: { models: CatalogModel[] }) {
  const [f, setF] = useState<Filters>(NO_FILTERS);
  const [sort, setSort] = useState<SortKey>("name");

  const shown = useMemo(() => sortModels(filterModels(models, f), sort), [models, f, sort]);
  const kinds = useMemo(() => kindFacets(models, f, MODEL_KINDS), [models, f]);
  const providers = useMemo(() => providerFacets(models, f), [models, f]);
  const dirty =
    f.query.trim() !== "" || f.kinds.length + f.providers.length + f.statuses.length > 0;

  if (models.length === 0) {
    return (
      <div className="empty">
        <h2>No models yet</h2>
        <p className="muted">
          Nothing has been declared, so no tier can point at anything and every
          AI request fails. Add a model below, then set up a tier.
        </p>
      </div>
    );
  }

  return (
    <>
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
          {(["ready", "unpriced", "undeclared"] as const).map((s) => (
            <button
              key={s}
              type="button"
              className="facet"
              aria-pressed={f.statuses.includes(s)}
              onClick={() => setF({ ...f, statuses: toggle(f.statuses, s) })}
            >
              {STATUS_LABEL[s]}
              <span className="count">
                {filterModels(models, { ...f, statuses: [s] }).length}
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
          <Card key={m.id} m={m} />
        ))}
      </div>
    </>
  );
}
