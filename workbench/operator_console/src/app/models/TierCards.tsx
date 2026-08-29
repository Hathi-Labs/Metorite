"use client";

// Tier cards — the whole model surface, in cards a person reads one at a time.
//
// 🔴 **This replaced a 4×5 grid.** The grid rendered twenty cells to say four
// facts, sixteen of them reading "not bound", and it still needed a horizontal
// scrollbar. A grid earns its place when the data is dense; this data is
// sparse, so it made a reader cross-reference a row against a column to learn
// one thing. Cards read one at a time and show only what exists.
//
// ⚠️ **An unset job is hidden behind "Add a job", not rendered as an empty
// box.** Sixteen empty boxes is not information — it is the absence of
// information, drawn at the same weight as the presence of it.
//
// ⚠️ **The model list offers only models that can do the job.** That is what
// makes "cannot serve" unreachable by hand: the old form was free text, and a
// typo produced a tier that looked correct and failed on the first request.

import { useState } from "react";

import { categoricalChip, providerGlyph } from "@/lib/categorical";
import { capableModelsFor, providerOf, type CellState } from "@/lib/readiness";
import { chipClass } from "@/lib/tone";

export type Task = { slug: string; label: string; natural_unit: string };

/** Plain words. No word here needs looking up. */
const STATE: Record<CellState, { label: string; tone: string }> = {
  ready: { label: "working", tone: "ok" },
  broken: { label: "will fail", tone: "danger" },
  unpriced: { label: "no price", tone: "warn" },
  empty: { label: "not set", tone: "" },
};

function ProviderChip({ model }: { model: string }) {
  const p = providerOf(model);
  return (
    <span className={categoricalChip(p)} title={`Supplied by ${p}`}>
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
  // One row open at a time. An operator changing four things at once cannot
  // tell which refusal belongs to which.
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [addTask, setAddTask] = useState("");
  const [newTier, setNewTier] = useState("");
  const [showNew, setShowNew] = useState(false);

  const rowKey = (tier: string, task: string) => `${tier}::${task}`;

  const shown =
    newTier.trim() && !tiers.includes(newTier.trim())
      ? [...tiers, newTier.trim()]
      : tiers;

  function taskLabel(slug: string) {
    return tasks.find((t) => t.slug === slug)?.label ?? slug;
  }

  /** One job inside a card: the name, the model, and what it is doing. */
  function Job({ tier, task }: { tier: string; task: Task }) {
    const model = modelOf(tier, task.slug);
    const state = stateOf(tier, task.slug);
    const chip = STATE[state];
    const options = capableModelsFor(capabilities, task.slug);
    const isOpen = editing === rowKey(tier, task.slug);

    return (
      <div className="job">
        <div className="job-head">
          <span className="job-name">{task.label}</span>
          {!isOpen && model && (
            <span className={chipClass(chip.tone as never)}>{chip.label}</span>
          )}
        </div>

        {isOpen ? (
          <>
            <select
              aria-label={`Model for ${tier} ${task.slug}`}
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
                No model can do this job yet. Add one on the Models tab below.
              </p>
            )}
            <div className="job-actions">
              <button
                type="button"
                className="linklike"
                disabled={busy || !draft}
                onClick={() => {
                  onBind(tier, task.slug, draft);
                  setEditing(null);
                }}
              >
                Save
              </button>
              <button
                type="button"
                className="linklike"
                onClick={() => setEditing(null)}
              >
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            <span className="job-model">{model}</span>
            <div className="job-foot">
              {model && <ProviderChip model={model} />}
              <button
                type="button"
                className="linklike"
                onClick={() => {
                  setEditing(rowKey(tier, task.slug));
                  setDraft(model ?? "");
                }}
              >
                Change
              </button>
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <>
      <div className="pagehead" style={{ marginTop: 6 }}>
        <h2 style={{ margin: 0 }}>Your tiers</h2>
        <button
          type="button"
          className="linklike"
          onClick={() => setShowNew((v) => !v)}
        >
          {showNew ? "Cancel" : "+ Add a tier"}
        </button>
      </div>

      {showNew && (
        <div className="panel">
          <label htmlFor="new-tier">Name it</label>
          <input
            id="new-tier"
            placeholder="tier-fast"
            value={newTier}
            onChange={(e) => setNewTier(e.target.value)}
          />
          <p className="field-hint">
            A tier is a speed and quality setting a customer picks. Give it a
            name, then choose which model does each job.
          </p>
        </div>
      )}

      {shown.length === 0 ? (
        <div className="empty">
          <h2>No tiers yet</h2>
          <p className="muted">
            A tier is a speed and quality setting a customer picks — Fast,
            Balanced or Powerful. Add one, then choose which model answers.
          </p>
        </div>
      ) : (
        <div className="tier-grid">
          {shown.map((tier) => {
            const bound = tasks.filter((t) => modelOf(tier, t.slug) !== null);
            const unbound = tasks.filter((t) => modelOf(tier, t.slug) === null);
            return (
              <section className="tier-card" key={tier}>
                <header>
                  <h3>{tier}</h3>
                  <span className="muted small">
                    {bound.length === 0
                      ? "nothing set"
                      : `${bound.length} job${bound.length === 1 ? "" : "s"}`}
                  </span>
                </header>

                {bound.map((t) => (
                  <Job key={t.slug} tier={tier} task={t} />
                ))}

                {addingTo === tier ? (
                  <div className="job">
                    <label htmlFor={`add-${tier}`}>Which job?</label>
                    <select
                      id={`add-${tier}`}
                      value={addTask}
                      onChange={(e) => setAddTask(e.target.value)}
                    >
                      <option value="">Choose a job…</option>
                      {unbound.map((t) => (
                        <option key={t.slug} value={t.slug}>
                          {t.label}
                        </option>
                      ))}
                    </select>
                    <div className="job-actions">
                      <button
                        type="button"
                        className="linklike"
                        disabled={!addTask}
                        onClick={() => {
                          setEditing(rowKey(tier, addTask));
                          setDraft("");
                          setAddingTo(null);
                        }}
                      >
                        Next
                      </button>
                      <button
                        type="button"
                        className="linklike"
                        onClick={() => setAddingTo(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  unbound.length > 0 && (
                    <button
                      type="button"
                      className="linklike add-job"
                      onClick={() => {
                        setAddingTo(tier);
                        setAddTask("");
                      }}
                    >
                      + Add a job
                    </button>
                  )
                )}

                {/* An in-flight job that was just chosen from "Add a job". */}
                {editing?.startsWith(`${tier}::`) &&
                  unbound.some((t) => rowKey(tier, t.slug) === editing) && (
                    <Job
                      tier={tier}
                      task={
                        tasks.find(
                          (t) => rowKey(tier, t.slug) === editing,
                        ) as Task
                      }
                    />
                  )}
              </section>
            );
          })}
        </div>
      )}

      <p className="note">
        Changing a model keeps the old one on record, so an old invoice still
        shows what it charged. You need an open elevation window to save.
        {" "}
        {tasks.length > 0 && (
          <>Jobs are measured in {tasks.map((t) => t.natural_unit).filter((u, i, a) => a.indexOf(u) === i).join(", ")}.</>
        )}
      </p>
    </>
  );
}
