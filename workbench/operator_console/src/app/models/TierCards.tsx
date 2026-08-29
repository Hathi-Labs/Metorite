"use client";

// Tier cards — the write surface for "what does this tier run on".
//
// 🔴 **This replaces a form that made you TYPE the tier name and the model id.**
// Both had to be exact. Neither was listed anywhere on the page. A typo in the
// tier created a second tier nobody was routing to, and a typo in the model
// created a binding that looked correct and 500'd on the first real request.
//
// ⚠️ **The model list offers only CAPABLE models.** `capableModelsFor` reads
// the declared capabilities for that task, so the `broken` state — bound to a
// model that cannot serve the task — becomes unreachable by hand. That is the
// difference between validating input and making the mistake impossible.
//
// ⚠️ Ported from the customer product's deleted `TierCard.tsx` (PR #145) in
// SHAPE only. Its `PROVIDER_COLOURS` table was raw Tailwind palette classes,
// which `AGENTS.md` rule 7 forbids and which this app has no Tailwind to
// resolve — provider identity comes from the categorical ramp instead. Its
// `Test` button is not ported either: the endpoint it called
// (`/api/settings/llm/test`) was deleted with it, and a button that cannot
// test anything is worse than no button.

import { useState } from "react";

import { categoricalChip, providerGlyph } from "@/lib/categorical";
import { capableModelsFor, providerOf, type CellState } from "@/lib/readiness";
import { chipClass } from "@/lib/tone";

export type Task = { slug: string; label: string; natural_unit: string };

const STATE_CHIP: Record<CellState, { label: string; tone: string }> = {
  ready: { label: "servable", tone: "ok" },
  broken: { label: "cannot serve", tone: "danger" },
  unpriced: { label: "unpriced", tone: "warn" },
  empty: { label: "not bound", tone: "" },
};

function ProviderChip({ model }: { model: string }) {
  const p = providerOf(model);
  return (
    <span className={categoricalChip(p)} title={`Provider: ${p}`}>
      <span className="glyph">{providerGlyph(p)}</span>
      {p}
    </span>
  );
}

export default function TierCards({
  tiers,
  tasks,
  capabilities,
  stateOf,
  modelOf,
  busy,
  onBind,
}: {
  tiers: string[];
  tasks: Task[];
  capabilities: { model: string; task: string }[];
  stateOf: (tier: string, task: string) => CellState;
  modelOf: (tier: string, task: string) => string | null;
  busy: boolean;
  onBind: (tier: string, task: string, model: string) => void;
}) {
  // Which (tier, task) row is open for editing. One at a time: an operator
  // changing four things at once cannot tell which refusal belongs to which.
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [newTier, setNewTier] = useState("");

  const rowKey = (tier: string, task: string) => `${tier}::${task}`;

  function open(tier: string, task: string) {
    setEditing(rowKey(tier, task));
    setDraft(modelOf(tier, task) ?? "");
  }

  const shown = newTier.trim() && !tiers.includes(newTier.trim())
    ? [...tiers, newTier.trim()]
    : tiers;

  return (
    <>
      <div className="formrow" style={{ marginTop: 12 }}>
        <div className="field grow">
          <label htmlFor="new-tier">Add a tier</label>
          <input
            id="new-tier"
            placeholder="tier-fast"
            value={newTier}
            onChange={(e) => setNewTier(e.target.value)}
          />
          <p className="field-hint">
            A tier exists because a binding names it. Type a name to get a card,
            then point it at a model.
          </p>
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="empty">
          <h2>No tier is bound</h2>
          <p className="muted">
            The Router resolves a tier to a model on every request. Until one
            exists it has nothing to resolve, and every AI call fails.
          </p>
        </div>
      ) : (
        <div className="tier-grid">
          {shown.map((tier) => (
            <section className="tier-card" key={tier}>
              <header>
                <h3>{tier}</h3>
              </header>

              {tasks.map((t) => {
                const model = modelOf(tier, t.slug);
                const state = stateOf(tier, t.slug);
                const chip = STATE_CHIP[state];
                const options = capableModelsFor(capabilities, t.slug);
                const isOpen = editing === rowKey(tier, t.slug);

                return (
                  <div className="tier-task" key={t.slug}>
                    <div className="lhs">
                      <div className="task-name">
                        {t.label}
                        <span className="muted"> · {t.natural_unit}</span>
                      </div>
                      {isOpen ? (
                        <div className="formrow" style={{ marginTop: 6 }}>
                          <div className="field grow">
                            <select
                              aria-label={`Model for ${tier} ${t.slug}`}
                              value={draft}
                              onChange={(e) => setDraft(e.target.value)}
                            >
                              <option value="">Choose a model…</option>
                              {options.map((m) => (
                                <option key={m} value={m}>
                                  {m}
                                </option>
                              ))}
                            </select>
                            {options.length === 0 && (
                              <p className="field-hint">
                                No model declares this task yet. Declare a
                                capability first, on the Capabilities tab.
                              </p>
                            )}
                          </div>
                        </div>
                      ) : model ? (
                        <>
                          <span className="task-model">{model}</span>
                          <div className="cell-flags" style={{ marginTop: 4 }}>
                            <ProviderChip model={model} />
                            <span className={chipClass(chip.tone as never)}>
                              {chip.label}
                            </span>
                          </div>
                        </>
                      ) : (
                        <span className="task-model none">not bound</span>
                      )}
                    </div>

                    <div style={{ display: "flex", gap: 6, flex: "none" }}>
                      {isOpen ? (
                        <>
                          <button
                            type="button"
                            className="linklike"
                            disabled={busy || !draft}
                            onClick={() => {
                              onBind(tier, t.slug, draft);
                              setEditing(null);
                            }}
                          >
                            Bind
                          </button>
                          <button
                            type="button"
                            className="linklike"
                            onClick={() => setEditing(null)}
                          >
                            Cancel
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="linklike"
                          onClick={() => open(tier, t.slug)}
                        >
                          {model ? "Change" : "Assign"}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </section>
          ))}
        </div>
      )}

      <p className="note">
        Binding is an append, never an edit — the previous row stays so a past
        invoice can still be read against what it was charged on. Needs an
        elevation window.
      </p>
    </>
  );
}
