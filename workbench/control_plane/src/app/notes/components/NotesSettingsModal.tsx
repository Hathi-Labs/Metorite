"use client";

/**
 * NotesSettingsModal — everything configurable about the Note Taker, in one
 * place (the shape the task manager and email apps use: sectioned form, read
 * and written whole).
 *
 * The section that earns its keep is **per meeting type**. What deserves an
 * interruption differs completely by meeting: in a customer call a dropped
 * objection matters; in a 1:1 an unspoken concern does, and a sales nudge would
 * be actively wrong. Each type ships sensible guidance — shown inline, so you
 * can see what it *would* do before deciding to override it.
 */

import { useCloseOnBarTap } from "@/lib/phoneBarTap";
import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useState } from "react";
import { getNotesSettings, saveNotesSettings } from "../lib/api";
import type { NotesSettings, TemplateInfo } from "../lib/types";
import BotIdentitySection from "./BotIdentitySection";

const DEFAULTS: NotesSettings = {
  copilot_instructions: "",
  copilot_default_on: false,
  copilot_sensitivity: "normal",
  live_transcription: "auto",
  template_instructions: {},
  default_template: null,
  bot_name: null,
  auto_dispatch_tasks: true,
  auto_dispatch_emails: true,
  auto_dispatch_docs: true,
};

/** Streaming speech-to-text is billed per minute; the recording is transcribed
 *  afterwards either way. So the honest framing is what you give up, not just
 *  what you save. */
const LIVE_MODE_HELP: Record<string, string> = {
  auto:
    "Streams only while the copilot is listening — the saving. Meetings " +
    "without it still get a full transcript when they end, just no live " +
    "captions or agenda tracking along the way.",
  always:
    "Always streams. Live captions and agenda coverage on every meeting, at " +
    "a per-minute cost even when no agent is reading them.",
  never:
    "Never streams. Cheapest, and the copilot cannot run — it has nothing to " +
    "listen to.",
};

const DISPATCH_TOGGLES: {
  key: "auto_dispatch_tasks" | "auto_dispatch_emails" | "auto_dispatch_docs";
  label: string;
  help: string;
}[] = [
  {
    key: "auto_dispatch_tasks",
    label: "Create tasks automatically",
    help: "Confident action items become tasks in your task manager.",
  },
  {
    key: "auto_dispatch_emails",
    label: "Send committed emails automatically",
    help:
      "Only when the recipient is clearly one of the attendees — otherwise a " +
      "draft is left in your mailbox instead. Nothing is ever guessed.",
  },
  {
    key: "auto_dispatch_docs",
    label: "Draft committed documents automatically",
    help: "First drafts land in Artifacts, ready to edit.",
  },
];

const SENSITIVITY_HELP: Record<string, string> = {
  low: "Rarely speaks up — long gaps, few interjections.",
  normal: "Balanced. A handful of well-timed points per meeting.",
  high: "Chimes in readily. Best for calls you want closely coached.",
};

export default function NotesSettingsModal({ onClose }: { onClose: () => void }) {
  // A bar tap closes the window (MOB-9).
  useCloseOnBarTap(onClose);
  const [s, setS] = useState<NotesSettings>(DEFAULTS);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [openType, setOpenType] = useState<string | null>(null);
  // A failed load must BLOCK saving — otherwise the form shows defaults and
  // "Save" silently overwrites the user's real settings with them.
  const [loadFailed, setLoadFailed] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getNotesSettings()
      .then((p) => {
        if (!alive) return;
        setS({ ...DEFAULTS, ...p.settings });
        setTemplates(p.templates ?? []);
      })
      .catch(() => alive && setLoadFailed(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  function setOverride(key: string, value: string) {
    setS((prev) => {
      const next = { ...prev.template_instructions };
      if (value.trim()) next[key] = value;
      else delete next[key]; // cleared → fall back to the shipped default
      return { ...prev, template_instructions: next };
    });
  }

  async function onSave() {
    setSaving(true);
    setSaveError(null);
    try {
      await saveNotesSettings(s);
      onClose();
    } catch (e) {
      // Leave the form up so nothing is lost — but say why.
      setSaveError(String(e instanceof Error ? e.message : e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative flex max-h-[85vh] w-full max-w-2xl flex-col rounded-2xl border border-border bg-card shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Icon name="Settings2" className="h-4 w-4 text-primary" />
          <h2 className="flex-1 text-sm font-semibold">Note Taker settings</h2>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close"
          >
            <Icon name="X" className="h-4 w-4" />
          </button>
        </div>

        {loading ? (
          <div className="flex h-48 items-center justify-center">
            <Icon name="Loader2" className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        ) : loadFailed ? (
          <div className="px-4 py-8 text-center text-sm text-destructive">
            Couldn&apos;t load your settings — saving is disabled so they
            aren&apos;t overwritten. Close and try again.
          </div>
        ) : (
          <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
            {/* ── Notetaker account ───────────────────────────────────────
                First, because it gates whether joining works at all: Google
                refuses anonymous participants. Acts immediately (it's an
                action, not a form field) so it ignores Save. */}
            <BotIdentitySection />

            {/* ── After-meeting dispatch ──────────────────────────────── */}
            <section>
              <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <Icon name="Send" className="h-3.5 w-3.5" /> After each meeting
              </h3>
              <p className="mb-2 text-xs text-muted-foreground">
                Confident action items (≥80%) are dispatched automatically when
                notes are generated. Everything is auditable per item on the
                meeting&apos;s Actions tab.
              </p>
              <div className="space-y-2">
                {DISPATCH_TOGGLES.map((t) => (
                  <label key={t.key} className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      checked={s[t.key]}
                      onChange={(e) => setS({ ...s, [t.key]: e.target.checked })}
                      className="mt-0.5"
                    />
                    <span className="text-sm">
                      {t.label}
                      <span className="block text-xs text-muted-foreground">
                        {t.help}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </section>

            {/* ── Copilot ─────────────────────────────────────────────── */}
            <section>
              <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <Icon name="Sparkles" className="h-3.5 w-3.5" /> Meeting copilot
              </h3>
              <label className="mb-3 flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={s.copilot_default_on}
                  onChange={(e) =>
                    setS({ ...s, copilot_default_on: e.target.checked })
                  }
                  className="mt-0.5"
                />
                <span className="text-sm">
                  Turn the copilot on automatically
                  <span className="block text-xs text-muted-foreground">
                    Off by default — it never runs unless asked.
                  </span>
                </span>
              </label>

              <p className="mb-1 text-xs font-medium">How readily it interrupts</p>
              <div className="mb-1 flex gap-2">
                {(["low", "normal", "high"] as const).map((lvl) => (
                  <button
                    key={lvl}
                    onClick={() => setS({ ...s, copilot_sensitivity: lvl })}
                    className={`rounded-lg border px-3 py-1 text-xs capitalize ${
                      s.copilot_sensitivity === lvl
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border hover:bg-accent"
                    }`}
                  >
                    {lvl}
                  </button>
                ))}
              </div>
              <p className="mb-3 text-xs text-muted-foreground">
                {SENSITIVITY_HELP[s.copilot_sensitivity]}
              </p>

              <p className="mb-1 text-xs font-medium">Live transcription</p>
              <div className="mb-1 flex gap-2">
                {(
                  [
                    ["auto", "With the copilot"],
                    ["always", "Always"],
                    ["never", "Never"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    onClick={() => setS({ ...s, live_transcription: value })}
                    className={`rounded-lg border px-3 py-1 text-xs ${
                      s.live_transcription === value
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border hover:bg-accent"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="mb-3 text-xs text-muted-foreground">
                {LIVE_MODE_HELP[s.live_transcription] ?? LIVE_MODE_HELP.auto}
              </p>

              <p className="mb-1 text-xs font-medium">
                Standing instructions
                <span className="ml-1 font-normal text-muted-foreground">
                  — applied to every meeting
                </span>
              </p>
              <textarea
                value={s.copilot_instructions}
                onChange={(e) =>
                  setS({ ...s, copilot_instructions: e.target.value })
                }
                rows={4}
                placeholder={`e.g. I run a 3D-printing company selling to manufacturing teams.\nPrefer commercial framing. Never suggest a discount — steer to value.`}
                className="w-full resize-y rounded-lg border border-border bg-background p-2 text-sm outline-none focus:border-primary"
              />
            </section>

            {/* ── Per meeting type ────────────────────────────────────── */}
            <section>
              <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                By meeting type
              </h3>
              <p className="mb-2 text-xs text-muted-foreground">
                Each type already has guidance suited to it. Override only where
                you want something different.
              </p>
              <div className="space-y-1.5">
                {templates.map((t) => {
                  const override = s.template_instructions[t.key] ?? "";
                  const open = openType === t.key;
                  return (
                    <div
                      key={t.key}
                      className="rounded-lg border border-border"
                    >
                      <button
                        onClick={() => setOpenType(open ? null : t.key)}
                        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
                      >
                        <span className="flex-1 text-sm">{t.label}</span>
                        {override ? (
                          <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
                            customised
                          </span>
                        ) : (
                          <span className="text-[10px] text-muted-foreground">
                            default
                          </span>
                        )}
                        <span className="text-xs text-muted-foreground">
                          {open ? "−" : "+"}
                        </span>
                      </button>
                      {open && (
                        <div className="border-t border-border px-2.5 py-2">
                          <p className="mb-1.5 text-[11px] text-muted-foreground">
                            <span className="text-foreground">Default:</span>{" "}
                            {t.copilot_default}
                          </p>
                          <textarea
                            value={override}
                            onChange={(e) => setOverride(t.key, e.target.value)}
                            rows={3}
                            placeholder="Leave empty to use the default above."
                            className="w-full resize-y rounded-lg border border-border bg-background p-2 text-xs outline-none focus:border-primary"
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>

            {/* ── Defaults for new meetings ───────────────────────────── */}
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                New meetings
              </h3>
              <label className="mb-2 block text-xs font-medium">
                Default notes template
              </label>
              <select
                value={s.default_template ?? ""}
                onChange={(e) =>
                  setS({ ...s, default_template: e.target.value || null })
                }
                className="mb-3 w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
              >
                <option value="">Standard meeting</option>
                {templates.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </select>

              <label className="mb-1 block text-xs font-medium">
                Notetaker name in calls
              </label>
              <input
                value={s.bot_name ?? ""}
                onChange={(e) => setS({ ...s, bot_name: e.target.value || null })}
                placeholder="e.g. Varada's AI Notetaker"
                className="w-full rounded-lg border border-border bg-background px-2 py-1.5 text-sm outline-none focus:border-primary"
              />
              <p className="mt-1 text-[11px] text-muted-foreground">
                Shown to everyone in the call — people should be able to tell who
                is recording them.
              </p>
            </section>
          </div>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          {saveError && (
            <p className="mr-auto text-xs text-destructive">
              Couldn&apos;t save: {saveError}
            </p>
          )}
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-accent"
          >
            Cancel
          </button>
          <Button size="none" layout="" onClick={onSave} disabled={saving || loading || loadFailed} className="px-3 py-1.5 text-sm">
            {saving ? "Saving…" : "Save settings"}
          </Button>
        </div>
      </div>
    </div>
  );
}
