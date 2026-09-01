"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { useTaskStore } from "../lib/taskStore";
import { fetchStatusCatalog, type TaskSettings, type StatusCatalog } from "../lib/api";
import { formatStatus } from "../lib/utils";
import {
  COLUMNS,
  DEFAULT_VISIBLE,
  readColumnVisibility,
  setColumnVisible,
  subscribeColumns,
} from "../lib/columns";

// Task Manager settings (mirror of the email app's AI Settings): pick the
// model tier per AI function + behaviour toggles. The gate pattern is the
// house one: the wrapper reads the store flag and the panel mounts fresh on
// each open, so local state starts clean.
export function TaskSettingsModal() {
  const open = useTaskStore((s) => s.settingsModalOpen);
  if (!open) return null;
  return <SettingsPanel />;
}

interface LLMTierInfo {
  tier_name: string;
}

/** The per-function TIER pickers.
 *
 * ⚠️ Tiers only. The raw-model-id group that used to sit
 * beside them is gone (H-72, D32.7): customers never see a model, and
 * the Console refuses a bare model id with a 400.
 */
const MODEL_FIELDS: {
  key: keyof Pick<
    TaskSettings,
    "chatModel" | "atomizeModel" | "emailCaptureModel" | "clarifyModel"
  >;
  title: string;
  description: string;
  def: string;
}[] = [
  {
    key: "chatModel",
    title: "Assistant chat model",
    description:
      "The model the task-manager chat rail runs on. A powerful tier is " +
      "recommended — it drives tools (capture, clarify, organize, sync).",
    def: "tier-powerful",
  },
  {
    key: "atomizeModel",
    title: "Mind-dump atomizer model",
    description:
      "Splits pasted paragraphs into atomic captures and judges duplicates. " +
      "High-volume triage — a fast tier is recommended.",
    def: "tier-fast",
  },
  {
    key: "emailCaptureModel",
    title: "Email → task drafting model",
    description:
      "Turns an email into an actionable capture title + context when you " +
      "use “Add to Tasks” in the email app.",
    def: "tier-fast",
  },
  {
    key: "clarifyModel",
    title: "Clarify proposals model",
    description:
      "The model behind AI-powered Clarify — it reasons over your active " +
      "projects, team skills, and workspace stages to propose a disposition, " +
      "next action, and best owner. Applies when “AI-powered clarify” is on.",
    def: "tier-balanced",
  },
];

function SettingsPanel() {
  const close = useTaskStore((s) => s.closeSettings);
  const backend = useTaskStore((s) => s.backend);
  const settings = useTaskStore((s) => s.settings);
  const updateSettings = useTaskStore((s) => s.updateSettings);

  // Tier list + enabled models — the same sources the email settings use.
  const [tiers, setTiers] = useState<LLMTierInfo[]>([]);
   
  useEffect(() => {
    let cancelled = false;
    fetch("/api/settings/llm")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!cancelled && d?.tiers) setTiers(d.tiers);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
   

  return (
    <div
      className="chat-fade-in fixed inset-0 z-[80] flex items-end justify-center bg-black/50 p-0 sm:items-start sm:p-4 sm:pt-[8vh]"
      onClick={close}
    >
      <div
        className="flex max-h-full w-full max-w-xl flex-col overflow-hidden rounded-t-2xl border-t border-border bg-card shadow-2xl pb-safe sm:max-h-[85vh] sm:rounded-2xl sm:border sm:pb-0"
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Icon name="Settings2" className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold text-foreground">
            Task Manager settings
          </h2>
          <Button variant="ghost" size="icon-sm" radius="keep" layout="" type="button" onClick={close} aria-label="Close" className="ml-auto rounded-md">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex flex-col gap-5 overflow-y-auto p-4">
          {backend !== "live" && (
            <p className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
              The tasks backend isn&apos;t reachable — changes apply to this
              session only and won&apos;t persist.
            </p>
          )}

          {/* ── AI models ── */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="Sparkles" className="h-3.5 w-3.5" /> AI models
            </h3>
            <div className="flex flex-col gap-2">
              {MODEL_FIELDS.map((cfg) => (
                <div
                  key={cfg.key}
                  className="rounded-lg border border-border px-3 py-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium text-foreground">
                      {cfg.title}
                    </p>
                    <select
                      value={settings[cfg.key] || cfg.def}
                      onChange={(e) =>
                        void updateSettings({ [cfg.key]: e.target.value })
                      }
                      className="tech-transition w-52 rounded-md border border-border bg-background/60 px-2 py-1 text-xs text-foreground focus:border-primary/50 focus:outline-none"
                    >
                      {tiers.length > 0 ? (
                        <>
                          {tiers.length > 0 && (
                            <optgroup label="Tiers (auto-routing)">
                              {tiers.map((t) => (
                                <option key={t.tier_name} value={t.tier_name}>
                                  {t.tier_name}
                                  {t.tier_name === cfg.def ? " (default)" : ""}
                                </option>
                              ))}
                            </optgroup>
                          )}
                          {/* ⚠️ AN OPTGROUP OF RAW MODEL IDS WAS HERE, and
                              D32.7 is why it is not (H-72). **Customers never
                              see a model.** Tiers are the only vocabulary, and
                              a bare model id is REFUSED by the Console with a
                              400 rather than coerced — so a customer who
                              picked one here would lose their Tasks AI the day
                              `ROUTER_SERVING_ENABLED` flips.

                              ⚠️ This stops NEW ones. It does not heal a value
                              already saved: that still shows below, because
                              hiding it would leave somebody staring at a
                              picker that disagrees with what their tasks
                              actually run on. Which tier an existing model id
                              should become is a product decision (H-72). */}
                          {settings[cfg.key] &&
                            !tiers.some(
                              (t) => t.tier_name === settings[cfg.key],
                            ) && (
                              <option value={settings[cfg.key]}>
                                {settings[cfg.key]} (not a tier — ask us)
                              </option>
                            )}
                        </>
                      ) : (
                        <option value={settings[cfg.key] || cfg.def}>
                          {settings[cfg.key] || cfg.def} (default)
                        </option>
                      )}
                    </select>
                  </div>
                  <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
                    {cfg.description}
                  </p>
                </div>
              ))}
            </div>
          </section>

          {/* ── Clarify ── */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="Sparkles" className="h-3.5 w-3.5" /> Clarify
            </h3>
            <Toggle
              title="AI-powered clarify"
              description="Let the assistant reason over your ClickUp projects, team (skills, seniority, free hours), and workspace stages to propose the disposition, next action, and best owner. Off = the instant deterministic heuristic only (no AI round-trip on each clarify)."
              checked={settings.clarifyUseLlm}
              onChange={(v) => void updateSettings({ clarifyUseLlm: v })}
            />
          </section>

          {/* ── Capture ── */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="Inbox" className="h-3.5 w-3.5" /> Capture
            </h3>
            <Toggle
              title="Duplicate check on quick capture"
              description="After a capture lands, the AI compares it against your open items in the background — confident duplicates are skipped (undoable), lookalikes ask you. Mind-sweep review always checks."
              checked={settings.captureDedup}
              onChange={(v) => void updateSettings({ captureDedup: v })}
            />
          </section>

          {/* ── Sync ── */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="RefreshCw" className="h-3.5 w-3.5" /> Sync
            </h3>
            <div className="flex flex-col gap-2">
              <Toggle
                title="Keep workspaces synced in the background"
                description="Refresh your connected PM workspaces (tasks, projects, and team) on a schedule — even when the app is closed — so the assistant always reasons over current data. Off = only manual and on-open sync run."
                checked={settings.backgroundSync}
                onChange={(v) => void updateSettings({ backgroundSync: v })}
              />
              <Toggle
                title="Sync workspaces when Tasks opens"
                description="Pull the latest tasks from your connected PM workspaces (incremental) each time you open the app. Manual sync stays available per workspace."
                checked={settings.autoSyncOnOpen}
                onChange={(v) => void updateSettings({ autoSyncOnOpen: v })}
              />
              <Toggle
                title="Mirror completed tasks from workspaces"
                description="Import already-completed tasks from your connected PM workspaces into the board. Off (recommended) keeps a large finished backlog from swamping your active views — your own captures stay visible, and tasks you already track still flip to Done when closed upstream."
                checked={settings.mirrorDoneTasks}
                onChange={(v) => void updateSettings({ mirrorDoneTasks: v })}
              />
            </div>
            <p className="mt-2 px-1 text-[11px] text-muted-foreground">
              Per-workspace connections, schema refresh, and disconnect live in
              the Workspaces dialog.
            </p>
          </section>

          {/* ── Next Actions list columns ── */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="Columns3" className="h-3.5 w-3.5" /> Next Actions columns
            </h3>
            <p className="mb-2 px-1 text-[11px] text-muted-foreground">
              In the list view (desktop), Next Actions shows these as aligned
              columns. Hide the ones you don&rsquo;t want to see. On mobile the
              pills stay stacked under each task regardless.
            </p>
            <ColumnsEditor />
          </section>

          {/* ── Board (Kanban stages for Next Actions) ── */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="Columns3" className="h-3.5 w-3.5" /> Kanban stages
            </h3>
            <p className="mb-2 px-1 text-[11px] text-muted-foreground">
              One global set of stages for <span className="font-medium">all</span>{" "}
              Next Actions — the columns of the board and the groups in the list.
              Add, rename, reorder, or remove them below. Drag a card to move it
              between stages; the <span className="font-medium">last</span> stage
              marks a task done (and closes it in ClickUp).
            </p>
            <StageEditor
              stages={settings.workflowStages}
              onChange={(next) => void updateSettings({ workflowStages: next })}
            />
          </section>

          {/* ── ClickUp status → stage mapping ── */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="Columns3" className="h-3.5 w-3.5" /> ClickUp status mapping
            </h3>
            <p className="mb-2 px-1 text-[11px] text-muted-foreground">
              Next Actions shows your{" "}
              <span className="font-medium">{settings.workflowStages.length}</span>{" "}
              stages, not every raw ClickUp status. Map each ClickUp status to one
              of your stages: a synced task shows in the stage its status maps to,
              and dragging a card writes the mapped status back to ClickUp. Unmapped
              statuses are auto-guessed — confirm or adjust them below.
            </p>
            <StatusMappingEditor
              stages={settings.workflowStages}
              map={settings.statusStageMap}
              onChange={(next) => void updateSettings({ statusStageMap: next })}
            />
          </section>
        </div>
      </div>
    </div>
  );
}

function StageEditor({
  stages,
  onChange,
}: {
  stages: string[];
  onChange: (next: string[]) => void;
}) {
  const [adding, setAdding] = useState("");

  const rename = (idx: number, name: string) =>
    onChange(stages.map((s, i) => (i === idx ? name : s)));
  const remove = (idx: number) =>
    onChange(stages.filter((_, i) => i !== idx));
  const move = (idx: number, dir: -1 | 1) => {
    const j = idx + dir;
    if (j < 0 || j >= stages.length) return;
    const next = [...stages];
    [next[idx], next[j]] = [next[j], next[idx]];
    onChange(next);
  };
  const add = () => {
    const name = adding.trim();
    if (!name) return;
    onChange([...stages, name]);
    setAdding("");
  };

  return (
    <div className="flex flex-col gap-1.5">
      {stages.map((stage, idx) => {
        const isLast = idx === stages.length - 1;
        return (
          <div
            key={idx}
            className="flex items-center gap-1.5 rounded-lg border border-border px-2 py-1.5"
          >
            <div className="flex flex-col">
              <button
                type="button"
                aria-label="Move up"
                disabled={idx === 0}
                onClick={() => move(idx, -1)}
                className="tech-transition text-muted-foreground/60 hover:text-foreground disabled:opacity-30"
              >
                <Icon name="ChevronUp" className="h-3 w-3" />
              </button>
              <button
                type="button"
                aria-label="Move down"
                disabled={isLast}
                onClick={() => move(idx, 1)}
                className="tech-transition text-muted-foreground/60 hover:text-foreground disabled:opacity-30"
              >
                <Icon name="ChevronDown" className="h-3 w-3" />
              </button>
            </div>
            <input
              value={stage}
              onChange={(e) => rename(idx, e.target.value)}
              onBlur={(e) => {
                // Never allow an empty stage name — restore a placeholder.
                if (!e.target.value.trim()) rename(idx, `Stage ${idx + 1}`);
              }}
              className="min-w-0 flex-1 rounded-md bg-transparent px-1.5 py-1 text-sm text-foreground focus:bg-background focus:outline-none focus:ring-1 focus:ring-primary/40"
            />
            {isLast && (
              <span className="shrink-0 rounded-full bg-success/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-success">
                Done
              </span>
            )}
            <button
              type="button"
              aria-label={`Remove ${stage}`}
              disabled={stages.length <= 1}
              onClick={() => remove(idx)}
              className="tech-transition shrink-0 rounded p-1 text-muted-foreground/60 hover:bg-destructive/10 hover:text-destructive disabled:opacity-30"
            >
              <Icon name="X" className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
      <div className="flex items-center gap-1.5 rounded-lg border border-dashed border-border px-2 py-1.5">
        <Icon name="Plus" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <input
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); add(); }
          }}
          placeholder="Add a stage…"
          className="min-w-0 flex-1 bg-transparent px-0.5 py-1 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
        />
        {adding.trim() && (
          <Button size="none" radius="keep" layout="" type="button" onClick={add} className="shrink-0 rounded-md px-2 py-1 text-[11px]">
            Add
          </Button>
        )}
      </div>
    </div>
  );
}

/** Maps each unique ClickUp status (fetched from the connected projects) to one
 *  of the user's Next-Actions stages. Auto-guessed rows show their guess and are
 *  saved on first confirm; changing a dropdown persists the whole map. */
function StatusMappingEditor({
  stages,
  map,
  onChange,
}: {
  stages: string[];
  map: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const backend = useTaskStore((s) => s.backend);
  const [catalog, setCatalog] = useState<StatusCatalog | null>(null);
  // Only "live" fetches; start non-live already-resolved (no fetch, no spinner)
  // so the effect never has to synchronously flip loading off.
  const [loading, setLoading] = useState(backend === "live");
  const [error, setError] = useState(false);

  useEffect(() => {
    if (backend !== "live") return;
    let cancelled = false;
    void fetchStatusCatalog()
      .then((c) => !cancelled && setCatalog(c))
      .catch(() => !cancelled && setError(true))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [backend]);

  // The effective stage for a status: an explicit user map wins; else the
  // catalog's auto-guess. norm() matches the backend's normalized key.
  const norm = (s: string) => s.trim().toLowerCase();
  const stageFor = (entry: { status: string; stage: string }): string =>
    map[norm(entry.status)] ?? entry.stage;

  // Set one status → stage, persisting the FULL map (existing picks + this one,
  // keyed normalized) so an auto-guess the user touches becomes a real choice.
  const setStage = (status: string, stage: string) => {
    onChange({ ...map, [norm(status)]: stage });
  };

  const unmappedCount = useMemo(
    () => (catalog?.entries ?? []).filter((e) => !(norm(e.status) in map)).length,
    [catalog, map],
  );

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-1 py-2 text-[11px] text-muted-foreground">
        <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" /> Loading ClickUp statuses…
      </div>
    );
  }
  if (error || backend !== "live") {
    return (
      <p className="px-1 py-2 text-[11px] text-muted-foreground">
        Connect a ClickUp workspace to map its statuses.
      </p>
    );
  }
  if (!catalog || catalog.entries.length === 0) {
    return (
      <p className="px-1 py-2 text-[11px] text-muted-foreground">
        No ClickUp statuses found yet — sync a workspace and they&rsquo;ll appear
        here to map.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      {unmappedCount > 0 && (
        <p className="mb-0.5 inline-flex items-center gap-1 px-1 text-[11px] text-warning">
          <Icon name="AlertTriangle" className="h-3 w-3 shrink-0" />
          {unmappedCount} status{unmappedCount === 1 ? "" : "es"} auto-guessed —
          confirm or adjust.
        </p>
      )}
      {catalog.entries.map((entry) => {
        const isGuess = !(norm(entry.status) in map);
        return (
          <div
            key={entry.status}
            className="flex items-center gap-2 rounded-lg border border-border px-2.5 py-1.5"
          >
            <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground">
              {formatStatus(entry.status)}
              {isGuess && (
                <span className="ml-1.5 text-[10px] italic text-muted-foreground">
                  (guessed)
                </span>
              )}
            </span>
            <span className="shrink-0 text-muted-foreground/50">→</span>
            <select
              value={stageFor(entry)}
              onChange={(e) => setStage(entry.status, e.target.value)}
              className="tech-transition h-7 shrink-0 rounded-md border border-border bg-background pl-2 pr-6 text-xs text-foreground focus:border-primary focus:outline-none"
            >
              {stages.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        );
      })}
    </div>
  );
}

/** The show/hide toggles for the Next-Actions list columns. Reads the same
 *  localStorage-backed visibility the list uses (via useSyncExternalStore) so a
 *  flip here updates the list live. A per-browser display pref — no server. */
function ColumnsEditor() {
  const vis = useSyncExternalStore(
    subscribeColumns,
    readColumnVisibility,
    () => DEFAULT_VISIBLE,
  );
  return (
    <div className="flex flex-col gap-1.5">
      {COLUMNS.map((c) => {
        const on = vis[c.key];
        return (
          <div
            key={c.key}
            className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2"
          >
            <span className="text-sm font-medium text-foreground">
              {c.label}
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={on}
              aria-label={`Show ${c.label} column`}
              onClick={() => setColumnVisible(c.key, !on)}
              className={[
                "tech-transition relative inline-flex h-5 w-9 shrink-0 items-center rounded-full",
                on ? "bg-primary" : "bg-secondary",
              ].join(" ")}
            >
              <span
                className={[
                  "inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform",
                  on ? "translate-x-[18px]" : "translate-x-[2px]",
                ].join(" ")}
              />
            </button>
          </div>
        );
      })}
    </div>
  );
}

function Toggle({
  title,
  description,
  checked,
  onChange,
}: {
  title: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-border px-3 py-2.5">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
          {description}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={title}
        onClick={() => onChange(!checked)}
        className={[
          "tech-transition relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full",
          checked ? "bg-primary" : "bg-secondary",
        ].join(" ")}
      >
        <span
          className={[
            "inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform",
            checked ? "translate-x-[18px]" : "translate-x-[2px]",
          ].join(" ")}
        />
      </button>
    </div>
  );
}
