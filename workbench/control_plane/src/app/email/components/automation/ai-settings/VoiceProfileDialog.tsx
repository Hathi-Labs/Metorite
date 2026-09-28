"use client";

// The "Voice profile" dialog (AI Settings → Advanced Settings → Your voice).
//
// Three views in one modal:
//  - builder:  pick sources (Sent / Drafts) + a date range (presets or custom),
//              see a live count of what the build would study, and start it.
//  - building: live progress while the background job collects → analyzes →
//              synthesizes (polls GET /voice-profile/status).
//  - overview: the built profile — trait chips, the editable style guide the
//              drafter actually reads, an enable toggle, a "try it" sample
//              generator, and the knowledge entries the builder suggested
//              (approve / dismiss).

import Icon from "@/components/Icon";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  approveKnowledge, buildVoiceProfile, deleteKnowledge, deleteVoiceProfile,
  getVoiceProfile, getVoiceProfileStatus, listKnowledge, previewVoiceProfile,
  sampleVoiceProfile, saveVoiceProfile,
} from "../../../lib/api";
import {
  KnowledgeEntry, VoiceProfile, VoiceProfileBuildStatus, VoiceProfilePreview,
} from "../../../lib/types";
import { Modal, Toggle } from "../ui";
import { INPUT_BASE, INPUT_CLS } from "./common";

const BTN_SECONDARY =
  "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border " +
  "text-xs text-muted-foreground hover:text-foreground hover:bg-secondary " +
  "transition-colors disabled:opacity-50";
const BTN_PRIMARY =
  "flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-primary " +
  "text-primary-foreground text-xs font-medium hover:bg-primary/90 " +
  "transition-colors disabled:opacity-50";

/** YYYY-MM-DD for "months ago from today" (UTC-naive is fine for a picker). */
function monthsAgoISO(months: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}

const todayISO = () => new Date().toISOString().slice(0, 10);

const RANGE_PRESETS = [
  { key: "3m", label: "Last 3 months", months: 3 },
  { key: "6m", label: "Last 6 months", months: 6 },
  { key: "12m", label: "Last year", months: 12 },
  { key: "all", label: "All time", months: 0 },
  { key: "custom", label: "Custom…", months: -1 },
] as const;
type PresetKey = (typeof RANGE_PRESETS)[number]["key"];

const SAMPLE_SCENARIOS = [
  "Reply to a customer asking about pricing",
  "Give a colleague a quick status update",
  "Politely decline a meeting invitation",
  "Follow up on an email that got no reply",
];

const PHASE_LABELS: Record<string, string> = {
  collecting: "Collecting your emails…",
  analyzing: "Studying how you write…",
  synthesizing: "Distilling your voice profile…",
  knowledge: "Extracting knowledge suggestions…",
  done: "Done",
};

export function VoiceProfileDialog({
  accountId,
  profile,
  onProfileChange,
  onClose,
}: {
  accountId: string;
  profile: VoiceProfile;
  /** Parent keeps the summary card's description in sync. */
  onProfileChange: (p: VoiceProfile) => void;
  onClose: () => void;
}) {
  const building = profile.status === "BUILDING";
  const [view, setView] = useState<"overview" | "builder" | "building">(
    building ? "building" : profile.status === "READY" ? "overview" : "builder"
  );

  return (
    <Modal
      title="Voice profile"
      description="Teach the assistant to write like you, from emails you've already written."
      onClose={onClose}
      maxWidth="max-w-2xl"
    >
      {view === "builder" && (
        <BuilderView
          accountId={accountId}
          profile={profile}
          onStarted={() => setView("building")}
          onBack={
            profile.status === "READY" ? () => setView("overview") : undefined
          }
        />
      )}
      {view === "building" && (
        <BuildingView
          accountId={accountId}
          onDone={(p) => {
            onProfileChange(p);
            setView(p.status === "READY" ? "overview" : "builder");
          }}
        />
      )}
      {view === "overview" && (
        <OverviewView
          accountId={accountId}
          profile={profile}
          onProfileChange={onProfileChange}
          onRebuild={() => setView("builder")}
          onRemoved={onClose}
        />
      )}
    </Modal>
  );
}

// ── Builder ─────────────────────────────────────────────────────────────────

function BuilderView({
  accountId,
  profile,
  onStarted,
  onBack,
}: {
  accountId: string;
  profile: VoiceProfile;
  onStarted: () => void;
  onBack?: () => void;
}) {
  const [useSent, setUseSent] = useState(true);
  const [useDrafts, setUseDrafts] = useState(false);
  const [preset, setPreset] = useState<PresetKey>("12m");
  const [customStart, setCustomStart] = useState(monthsAgoISO(12));
  const [customEnd, setCustomEnd] = useState(todayISO());
  const [extractKnowledge, setExtractKnowledge] = useState(true);
  const [preview, setPreview] = useState<VoiceProfilePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sources = [
    ...(useSent ? ["sent"] : []),
    ...(useDrafts ? ["drafts"] : []),
  ];
  const presetDef = RANGE_PRESETS.find((p) => p.key === preset)!;
  const startDate =
    preset === "custom"
      ? customStart || undefined
      : presetDef.months > 0
        ? monthsAgoISO(presetDef.months)
        : undefined;
  const endDate = preset === "custom" ? customEnd || undefined : undefined;

  // Live count of what the build would study — debounced so dragging through
  // presets/dates doesn't fire a request per keystroke.
  const previewKey = `${sources.join(",")}|${startDate ?? ""}|${endDate ?? ""}`;
  const keyRef = useRef(previewKey);
  useEffect(() => {
    keyRef.current = previewKey;
    const t = setTimeout(() => {
      if (sources.length === 0) {
        setPreview(null);
        return;
      }
      setPreviewLoading(true);
      previewVoiceProfile({ accountId, startDate, endDate, sources })
        .then((p) => {
          if (keyRef.current === previewKey) setPreview(p);
        })
        .catch(() => setPreview(null))
        .finally(() => setPreviewLoading(false));
    }, 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId, previewKey]);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      await buildVoiceProfile({
        accountId,
        startDate,
        endDate,
        sources,
        extractKnowledge,
      });
      onStarted();
    } catch (e) {
      setError((e as Error).message || "Could not start the build.");
      setStarting(false);
    }
  };

  return (
    <div className="space-y-4">
      {onBack && (
        <button onClick={onBack} className={BTN_SECONDARY}>
          <Icon name="ChevronLeft" size={12} /> Back to profile
        </button>
      )}
      {profile.status === "FAILED" && profile.last_error && (
        <div className="text-[11px] text-destructive bg-destructive/10 rounded-md px-2.5 py-2">
          Last build failed: {profile.last_error}
        </div>
      )}

      <section>
        <h4 className="text-xs font-semibold text-foreground mb-1">
          Learn from
        </h4>
        <div className="space-y-1.5">
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={useSent}
              onChange={(e) => setUseSent(e.target.checked)}
              className="accent-primary mt-0.5"
            />
            <span>
              <span className="text-xs text-foreground">Sent mail</span>
              <span className="block text-[11px] text-muted-foreground">
                Emails you actually sent — the most reliable signal of your
                voice.
              </span>
            </span>
          </label>
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={useDrafts}
              onChange={(e) => setUseDrafts(e.target.checked)}
              className="accent-primary mt-0.5"
            />
            <span>
              <span className="text-xs text-foreground">Drafts</span>
              <span className="block text-[11px] text-muted-foreground">
                Unsent drafts count too — but note the folder can include
                drafts the assistant wrote, not just yours.
              </span>
            </span>
          </label>
        </div>
      </section>

      <section>
        <h4 className="text-xs font-semibold text-foreground mb-1">
          Date range
        </h4>
        <p className="text-[11px] text-muted-foreground mb-2">
          Pick the period that sounds like you today — if your style changed,
          leave old mail out.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {RANGE_PRESETS.map((p) => (
            <button
              key={p.key}
              onClick={() => setPreset(p.key)}
              className={`px-2.5 py-1.5 rounded-lg border text-[11px] transition-colors ${
                preset === p.key
                  ? "border-primary bg-primary/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground hover:bg-secondary"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
        {preset === "custom" && (
          <div className="flex items-center gap-2 mt-2">
            <input
              type="date"
              value={customStart}
              max={customEnd || undefined}
              onChange={(e) => setCustomStart(e.target.value)}
              className={INPUT_BASE}
              aria-label="From date"
            />
            <span className="text-xs text-muted-foreground">to</span>
            <input
              type="date"
              value={customEnd}
              min={customStart || undefined}
              onChange={(e) => setCustomEnd(e.target.value)}
              className={INPUT_BASE}
              aria-label="To date"
            />
          </div>
        )}
      </section>

      <div className="text-[11px] text-muted-foreground bg-secondary/40 rounded-md px-2.5 py-2 min-h-[34px] flex items-center gap-1.5">
        {sources.length === 0 ? (
          "Pick at least one source."
        ) : previewLoading || !preview ? (
          <>
            <Icon name="Loader2" className="animate-spin" size={11} /> Counting emails in
            this range…
          </>
        ) : preview.total === 0 ? (
          "No emails found in this range — widen it or check the sources."
        ) : (
          <span>
            <span className="text-foreground font-medium">
              {preview.total.toLocaleString()}
            </span>{" "}
            email{preview.total === 1 ? "" : "s"} in range
            {useSent && useDrafts
              ? ` (${preview.sent.toLocaleString()} sent, ${preview.drafts.toLocaleString()} drafts)`
              : ""}
            {preview.total > preview.cap
              ? ` — the assistant will study the ${preview.cap} most recent.`
              : "."}
          </span>
        )}
      </div>

      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={extractKnowledge}
          onChange={(e) => setExtractKnowledge(e.target.checked)}
          className="accent-primary mt-0.5"
        />
        <span>
          <span className="text-xs text-foreground">
            Also suggest knowledge entries
          </span>
          <span className="block text-[11px] text-muted-foreground">
            Recurring facts found in your mail (your role, products, policies…)
            are proposed for the knowledge base — nothing is used until you
            approve it.
          </span>
        </span>
      </label>

      {error && (
        <div className="text-[11px] text-destructive bg-destructive/10 rounded-md px-2.5 py-2">
          {error}
        </div>
      )}

      <div className="flex items-center justify-between pt-1">
        <span className="text-[10px] text-muted-foreground">
          Only your own words are studied — quoted replies from others are
          stripped out.
        </span>
        <button
          onClick={start}
          disabled={starting || sources.length === 0 || preview?.total === 0}
          className={BTN_PRIMARY}
        >
          {starting ? (
            <Icon name="Loader2" className="animate-spin" size={13} />
          ) : (
            <Icon name="Wand2" size={13} />
          )}
          {profile.status === "READY" ? "Rebuild profile" : "Build my profile"}
        </button>
      </div>
    </div>
  );
}

// ── Building (progress) ─────────────────────────────────────────────────────

function BuildingView({
  accountId,
  onDone,
}: {
  accountId: string;
  onDone: (profile: VoiceProfile) => void;
}) {
  const [job, setJob] = useState<VoiceProfileBuildStatus | null>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    const finish = async () => {
      if (doneRef.current) return;
      doneRef.current = true;
      const p = await getVoiceProfile(accountId).catch(() => null);
      if (!cancelled && p) onDone(p);
    };
    const tick = async () => {
      try {
        const s = await getVoiceProfileStatus(accountId);
        if (cancelled) return;
        setJob(s);
        // idle = the tracker never saw this build (server restarted) — fall
        // back to the profile row, which records BUILDING→FAILED/READY.
        if (s.status === "done" || s.status === "error" || s.status === "idle") {
          await finish();
        }
      } catch {
        /* transient — keep polling */
      }
    };
    tick();
    const iv = setInterval(tick, 1500);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountId]);

  const phase = job?.phase || "collecting";
  const pct =
    phase === "analyzing" && job?.total
      ? Math.round(((job.processed || 0) / job.total) * 100)
      : phase === "synthesizing" || phase === "knowledge"
        ? 100
        : 0;

  return (
    <div className="py-6 flex flex-col items-center gap-3 text-center">
      {job?.status === "error" ? (
        <>
          <Icon name="X" size={20} className="text-destructive" />
          <p className="text-xs text-destructive max-w-sm">{job.error}</p>
        </>
      ) : (
        <>
          <Icon name="Loader2" className="animate-spin text-primary" size={22} />
          <p className="text-xs text-foreground">
            {PHASE_LABELS[phase] || "Working…"}
          </p>
          {phase === "analyzing" && (job?.total ?? 0) > 0 && (
            <div className="w-56">
              <div className="h-1.5 rounded-full bg-secondary overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <p className="text-[10px] text-muted-foreground mt-1">
                {job?.processed ?? 0} / {job?.total} batches
                {job?.sample_count
                  ? ` · ${job.sample_count} emails`
                  : ""}
              </p>
            </div>
          )}
          <p className="text-[10px] text-muted-foreground max-w-xs">
            This runs in the background — you can close this dialog and come
            back.
          </p>
        </>
      )}
    </div>
  );
}

// ── Overview ────────────────────────────────────────────────────────────────

function OverviewView({
  accountId,
  profile,
  onProfileChange,
  onRebuild,
  onRemoved,
}: {
  accountId: string;
  profile: VoiceProfile;
  onProfileChange: (p: VoiceProfile) => void;
  onRebuild: () => void;
  onRemoved: () => void;
}) {
  const [guide, setGuide] = useState(profile.style_guide);
  const [editingGuide, setEditingGuide] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const t = profile.traits || {};
  const scalarChips = [
    t.tone,
    t.formality,
    t.typical_length,
    t.emoji_usage && `emoji: ${t.emoji_usage}`,
  ].filter(Boolean) as string[];

  const patch = async (p: { enabled?: boolean; styleGuide?: string }) => {
    setBusy(true);
    setError(null);
    try {
      const next = await saveVoiceProfile({ accountId, ...p });
      onProfileChange(next);
    } catch (e) {
      setError((e as Error).message || "Failed to save.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (
      !confirm(
        "Remove the voice profile? Drafts go back to the generic style. " +
          "Suggested knowledge entries you haven't approved are removed too."
      )
    )
      return;
    setBusy(true);
    try {
      await deleteVoiceProfile(accountId);
      onRemoved();
    } catch (e) {
      setError((e as Error).message || "Failed to remove.");
      setBusy(false);
    }
  };

  const meta = [
    `${profile.analyzed_count} email${profile.analyzed_count === 1 ? "" : "s"}`,
    profile.sources.join(" + "),
    profile.range_start || profile.range_end
      ? `${profile.range_start ?? "…"} → ${profile.range_end ?? "today"}`
      : "all time",
    profile.built_at ? `built ${profile.built_at.slice(0, 10)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xs text-foreground font-medium">
            <Icon name="AudioLines" size={13} className="text-primary" /> Learned from{" "}
            {meta}
          </div>
          {!profile.enabled && (
            <p className="text-[11px] text-warning mt-0.5">
              Turned off — drafts aren&apos;t using this voice right now.
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button onClick={onRebuild} className={BTN_SECONDARY} disabled={busy}>
            <Icon name="RefreshCcw" size={12} /> Rebuild
          </button>
          <Toggle
            enabled={profile.enabled}
            onChange={(v) => patch({ enabled: v })}
            disabled={busy}
            title={profile.enabled ? "Profile is on" : "Profile is off"}
          />
        </div>
      </div>

      {scalarChips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {scalarChips.map((c) => (
            <span
              key={c}
              className="px-2 py-1 rounded-md text-[11px] bg-primary/10 border border-primary/20 text-foreground"
            >
              {c}
            </span>
          ))}
        </div>
      )}

      {(t.greetings?.length || t.signoffs?.length || t.common_phrases?.length)
        ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {(
            [
              ["Greetings", t.greetings],
              ["Sign-offs", t.signoffs],
              ["Your phrases", t.common_phrases],
            ] as const
          ).map(
            ([label, items]) =>
              !!items?.length && (
                <div
                  key={label}
                  className="border border-border rounded-lg px-2.5 py-2"
                >
                  <div className="text-[9px] uppercase tracking-wide text-muted-foreground mb-1">
                    {label}
                  </div>
                  <div className="space-y-0.5">
                    {items.slice(0, 4).map((it) => (
                      <div
                        key={it}
                        className="text-[11px] text-foreground/85 truncate"
                        title={it}
                      >
                        {it}
                      </div>
                    ))}
                  </div>
                </div>
              )
          )}
        </div>
      ) : null}

      <section>
        <div className="flex items-center justify-between mb-1">
          <h4 className="text-xs font-semibold text-foreground">
            Style guide{" "}
            <span className="font-normal text-muted-foreground">
              — what drafts actually follow
            </span>
          </h4>
          {!editingGuide && (
            <button
              onClick={() => {
                setGuide(profile.style_guide);
                setEditingGuide(true);
              }}
              className="flex items-center gap-1 text-[11px] text-primary hover:opacity-80"
            >
              <Icon name="Pencil" size={11} /> Edit
            </button>
          )}
        </div>
        {editingGuide ? (
          <div className="space-y-2">
            <textarea
              value={guide}
              onChange={(e) => setGuide(e.target.value)}
              rows={7}
              className={`${INPUT_CLS} resize-none`}
            />
            <div className="flex items-center gap-2">
              <button
                onClick={async () => {
                  await patch({ styleGuide: guide });
                  setEditingGuide(false);
                }}
                disabled={busy}
                className={BTN_PRIMARY}
              >
                {busy ? (
                  <Icon name="Loader2" className="animate-spin" size={13} />
                ) : (
                  <Icon name="Check" size={13} />
                )}
                Save
              </button>
              <button
                onClick={() => setEditingGuide(false)}
                className={BTN_SECONDARY}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <pre className="text-[11px] text-foreground/85 bg-secondary/40 rounded-md px-2.5 py-2 whitespace-pre-wrap break-words font-sans max-h-44 overflow-y-auto">
            {profile.style_guide || "No style guide — rebuild the profile."}
          </pre>
        )}
      </section>

      <TryItSection accountId={accountId} />

      <SuggestedKnowledge
        accountId={accountId}
        onCountChange={(n) =>
          onProfileChange({ ...profile, suggested_knowledge: n })
        }
      />

      {error && (
        <div className="text-[11px] text-destructive bg-destructive/10 rounded-md px-2.5 py-2">
          {error}
        </div>
      )}

      <div className="pt-1 border-t border-border">
        <button
          onClick={remove}
          disabled={busy}
          className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-destructive transition-colors pt-2 disabled:opacity-50"
        >
          <Icon name="Trash2" size={12} /> Remove profile
        </button>
      </div>
    </div>
  );
}

// ── Try it ──────────────────────────────────────────────────────────────────

function TryItSection({ accountId }: { accountId: string }) {
  const [scenario, setScenario] = useState(SAMPLE_SCENARIOS[0]);
  const [custom, setCustom] = useState("");
  const [sample, setSample] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const generate = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await sampleVoiceProfile(
        accountId,
        scenario === "custom" ? custom : scenario
      );
      setSample(res.sample);
    } catch (e) {
      setErr((e as Error).message || "Could not generate a sample.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="border border-border rounded-lg px-3 py-2.5">
      <h4 className="flex items-center gap-1.5 text-xs font-semibold text-foreground mb-1">
        <Icon name="Sparkles" size={12} className="text-primary" /> Try it
      </h4>
      <p className="text-[11px] text-muted-foreground mb-2">
        Generate a sample email in this voice to judge the profile before it
        writes real drafts.
      </p>
      <div className="flex items-center gap-2">
        <select
          value={scenario}
          onChange={(e) => setScenario(e.target.value)}
          className={`${INPUT_BASE} flex-1 min-w-0`}
        >
          {SAMPLE_SCENARIOS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
          <option value="custom">Something else…</option>
        </select>
        <button onClick={generate} disabled={busy} className={BTN_SECONDARY}>
          {busy ? (
            <Icon name="Loader2" className="animate-spin" size={12} />
          ) : (
            <Icon name="Wand2" size={12} />
          )}
          Generate
        </button>
      </div>
      {scenario === "custom" && (
        <input
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          placeholder="Describe the email to write…"
          className={`${INPUT_CLS} mt-2`}
        />
      )}
      {err && <p className="text-[10px] text-destructive mt-2">{err}</p>}
      {sample && (
        <pre className="mt-2 text-[11px] text-foreground/90 bg-secondary/40 rounded-md px-2.5 py-2 whitespace-pre-wrap break-words font-sans max-h-48 overflow-y-auto">
          {sample}
        </pre>
      )}
    </section>
  );
}

// ── Suggested knowledge ─────────────────────────────────────────────────────

function SuggestedKnowledge({
  accountId,
  onCountChange,
}: {
  accountId: string;
  onCountChange: (n: number) => void;
}) {
  const [entries, setEntries] = useState<KnowledgeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  // Ref'd so the (inline) parent callback doesn't retrigger the load effect.
  const onCountRef = useRef(onCountChange);
  useEffect(() => {
    onCountRef.current = onCountChange;
  }, [onCountChange]);

  const load = useCallback(() => {
    listKnowledge(accountId)
      .then((all) => {
        const suggested = all.filter((e) => e.status === "suggested");
        setEntries(suggested);
        onCountRef.current(suggested.length);
      })
      .catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [accountId]);

  useEffect(load, [load]);

  const act = async (entry: KnowledgeEntry, approve: boolean) => {
    if (!entry.id) return;
    setBusy(entry.id);
    try {
      if (approve) await approveKnowledge(entry.id);
      else await deleteKnowledge(entry.id);
      const next = entries.filter((e) => e.id !== entry.id);
      setEntries(next);
      onCountRef.current(next.length);
    } catch {
      load();
    } finally {
      setBusy(null);
    }
  };

  if (loading || entries.length === 0) return null;

  return (
    <section className="border border-warning/30 bg-warning/5 rounded-lg px-3 py-2.5">
      <h4 className="text-xs font-semibold text-warning mb-1">
        Suggested knowledge · {entries.length}
      </h4>
      <p className="text-[11px] text-muted-foreground mb-2">
        Facts found in your mail. Approved entries join the knowledge base and
        feed future drafts; dismissed ones are discarded.
      </p>
      <div className="space-y-1.5">
        {entries.map((e) => (
          <div
            key={e.id}
            className="flex items-start gap-2 bg-card border border-border rounded-lg px-2.5 py-2"
          >
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium text-foreground truncate">
                {e.title}
              </div>
              <div className="text-[11px] text-muted-foreground line-clamp-2">
                {e.content}
              </div>
            </div>
            <div className="flex items-center gap-1.5 flex-shrink-0 pt-0.5">
              {busy === e.id ? (
                <Icon name="Loader2" className="animate-spin text-muted-foreground" size={13} />
              ) : (
                <>
                  <button
                    onClick={() => act(e, true)}
                    title="Approve — add to the knowledge base"
                    className="text-emerald-500 hover:text-emerald-400"
                  >
                    <Icon name="Check" size={14} />
                  </button>
                  <button
                    onClick={() => act(e, false)}
                    title="Dismiss"
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Icon name="X" size={14} />
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
