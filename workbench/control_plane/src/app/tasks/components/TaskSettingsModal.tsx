"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useState, useSyncExternalStore } from "react";
import { useTaskStore } from "../lib/taskStore";
import type { TaskSettings } from "../lib/api";
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
      "use “Add to My Tasks” in the email app.",
    def: "tier-fast",
  },
  {
    key: "clarifyModel",
    title: "Clarify proposals model",
    description:
      "The model behind AI-powered Clarify — it reasons over your active " +
      "projects, team skills, and project lanes to propose a disposition, " +
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
            My Tasks settings
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
              description="Let the assistant reason over your projects, team (skills, seniority, free hours), and project lanes to propose the disposition, next action, and best owner. Off = the instant deterministic heuristic only (no AI round-trip on each clarify)."
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

          {/* ── Stages (D73.9) ── */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <Icon name="Columns3" className="h-3.5 w-3.5" /> Stages
            </h3>
            <p className="px-1 text-[11px] leading-snug text-muted-foreground">
              Stages come from each project&rsquo;s lanes in Projects. Next
              Actions groups them by category: To do, In progress and Done. Each
              task keeps its own lane name, so &ldquo;Building&rdquo; in one project
              and &ldquo;In progress&rdquo; in another both sit under In progress.
              To add or rename a lane, edit the project in Projects.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}

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
