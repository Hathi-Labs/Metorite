"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon, type ThemedIcon } from "@/components/Icon";
import { useEffect, useRef, useState } from "react";
import { useTaskStore } from "../lib/taskStore";
import { apiAtomize } from "../lib/api";
import { snoozeOptions, detectDateHint, matchWhere } from "../lib/utils";
import { CAPTURE_TRIGGERS } from "../lib/mockData";
import { AttachmentComposer } from "./AttachmentComposer";
import type { TaskAttachment } from "../lib/types";
import { useVisualViewport } from "../lib/useVisualViewport";
import { captureDestinations, destinations, useCompanyTree } from "../lib/companyTree";
import { parseProjectToken } from "../lib/quickAdd";

// Ubiquitous capture (C2) + Mind Sweep (C3/C4). A global palette openable from
// any Tasks view via a hotkey (C) or a button. Single mode = rapid-fire
// quick add; Sweep mode = multi-line brain dump (one item per line) with the
// GTD Incompletion Trigger List as memory-joggers. Capture stays PURE — no
// clarify here (GTD keeps the two stages separate).
// Gate: mounts the panel fresh each time the palette opens, so its local state
// starts clean without a reset-in-effect.
// Cheap "is this really several tasks?" pre-check for a single capture. Short
// lines never trigger (almost always one action); only longer text with joining
// words / list separators is worth an atomizer round-trip. The atomizer is the
// real arbiter — this only decides whether to spend the call.
function looksMultiTask(t: string): boolean {
  if (t.includes("\n")) return true; // several lines pasted into the single box
  const words = t.split(/\s+/).filter(Boolean).length;
  if (words < 6) return false;
  if (/[;•]/.test(t)) return true;
  if (/\b(?:and then|then|also|as well as|followed by)\b/i.test(t)) return true;
  if (/,\s*(?:and|then)\b/i.test(t)) return true;
  if (t.length > 80 && /\band\b/i.test(t)) return true;
  return false;
}

export function QuickCapture() {
  const open = useTaskStore((s) => s.quickCaptureOpen);
  if (!open) return null;
  return <QuickCapturePanel />;
}

function QuickCapturePanel() {
  const storeMode = useTaskStore((s) => s.quickCaptureMode);
  const close = useTaskStore((s) => s.closeQuickCapture);
  const captureLine = useTaskStore((s) => s.captureLine);
  const captureMany = useTaskStore((s) => s.captureMany);
  // S6g — `#Name` files straight onto a project, the way the Inbox box does,
  // through the store's one capture-line flow (`captureLine`).
  const areas = useTaskStore((s) => s.areas);
  const projects = useTaskStore((s) => s.projects);
  const liveBackend = useTaskStore((s) => s.backend) === "live";
  const { roots } = useCompanyTree(liveBackend);
  const targets = captureDestinations({ areas, tree: destinations(roots), projects });

  const vp = useVisualViewport();
  const [mode, setMode] = useState<"single" | "sweep">(storeMode);
  const [value, setValue] = useState("");
  const [added, setAdded] = useState(0);
  const [pendingAtts, setPendingAtts] = useState<TaskAttachment[]>([]);
  // Optional capture-time date: a "remind" tickler (defer_until — hidden until
  // then, resurfaces in the inbox) or a "due" deadline (due_at + hard date).
  const [dateIso, setDateIso] = useState("");
  const [dateKind, setDateKind] = useState<"remind" | "due">("remind");
  // Sweep has a review gate: write → review the parsed items → add.
  const [phase, setPhase] = useState<"write" | "review">("write");
  /** Review candidates. Verdicts come from the AI atomizer: "duplicate"
   *  (confident same — excluded by default), "similar" (asks the user),
   *  "new". The instant line-split shows first; the atomizer upgrades the
   *  list in place unless the user already edited it. */
  const [reviewItems, setReviewItems] = useState<
    { title: string; include: boolean; verdict: "new" | "similar" | "duplicate"; matchTitle?: string; matchDisposition?: string; matchSource?: string }[]
  >([]);
  const [atomizing, setAtomizing] = useState(false);
  // Single-capture smart-breakdown: `verifying` = the atomizer round-trip is in
  // flight; `splitFromSingle` = the review being shown was reached by splitting
  // a single capture (so we also offer "keep as one").
  const [verifying, setVerifying] = useState(false);
  const [splitFromSingle, setSplitFromSingle] = useState(false);
  const reviewEditedRef = useRef(false);
  const backend = useTaskStore((s) => s.backend);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const switchMode = (m: "single" | "sweep") => {
    setMode(m);
    setPhase("write");
    setSplitFromSingle(false);
  };

  // Escape closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  const lineCount = value
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean).length;

  const doCaptureSingle = (t: string) => {
    // A captured date rides along: "remind" → tickler (defer_until); "due" →
    // deadline (due_at + hard date). No date → pure capture, unchanged.
    const dates = dateIso
      ? dateKind === "remind"
        ? { deferUntil: dateIso }
        : { dueAt: dateIso, isHardDate: true }
      : undefined;
    captureLine(t, {
      targets,
      attachments: pendingAtts.length ? pendingAtts : undefined,
      dates,
    });
    setValue("");
    setPendingAtts([]);
    setDateIso("");
    setAdded((a) => a + 1);
    inputRef.current?.focus();
  };

  // Before filing a single item, sanity-check whether it's actually several
  // distinct tasks — if so, drop into the mind-sweep review to split it rather
  // than burying multiple actions in one inbox line. One task → files as-is.
  const verifyAndMaybeSplit = async (t: string) => {
    setVerifying(true);
    try {
      const { items } = await apiAtomize(t);
      const distinct = items.filter((i) => i.title.trim());
      if (distinct.length > 1) {
        reviewEditedRef.current = false;
        setSplitFromSingle(true);
        setReviewItems(distinct.map((it) => ({
          title: it.title,
          include: it.verdict !== "duplicate",
          verdict: it.verdict,
          matchTitle: it.matchTitle,
          matchDisposition: it.matchDisposition,
          matchSource: it.matchSource,
        })));
        setMode("sweep");
        setPhase("review");
      } else {
        doCaptureSingle(t); // one task after all
      }
    } catch {
      doCaptureSingle(t); // atomizer unavailable — just file it
    } finally {
      setVerifying(false);
    }
  };

  const submitSingle = () => {
    const t = value.trim();
    if (!t || verifying) return;
    // Multi-task detection only when it's worth it: live backend, no per-item
    // date/attachment already chosen (those signal a single item), and the text
    // reads like several actions. Everything else files instantly.
    if (backend === "live" && !pendingAtts.length && !dateIso && looksMultiTask(t)) {
      void verifyAndMaybeSplit(t);
      return;
    }
    doCaptureSingle(t);
  };

  // Sweep → review gate (the §2.1 AI seam, now live). The instant line-split
  // renders immediately; the server atomizer (LLM splitting + duplicate
  // check against open items, deterministic fallback) upgrades the list in
  // place — unless the user already started editing (their edits win).
  const goToReview = () => {
    const lines = value
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter(Boolean);
    if (!lines.length) return;
    reviewEditedRef.current = false;
    setSplitFromSingle(false); // a genuine sweep, not a single-capture split
    setReviewItems(lines.map((t) => ({ title: t, include: true, verdict: "new" as const })));
    setPhase("review");
    if (backend !== "live") return;
    setAtomizing(true);
    apiAtomize(value)
      .then(({ items }) => {
        if (reviewEditedRef.current || !items.length) return;
        setReviewItems(items.map((it) => ({
          title: it.title,
          // Confident duplicates are excluded by default (still listed, one
          // tap re-includes); "similar" stays included but flagged.
          include: it.verdict !== "duplicate",
          verdict: it.verdict,
          matchTitle: it.matchTitle,
          matchDisposition: it.matchDisposition,
          matchSource: it.matchSource,
        })));
      })
      .catch(() => { /* keep the line split — atomizer is an upgrade */ })
      .finally(() => setAtomizing(false));
  };
  const reviewCount = reviewItems.filter((l) => l.include && l.title.trim()).length;
  const commitReview = () => {
    const clean = reviewItems
      .filter((l) => l.include)
      .map((l) => l.title.trim())
      .filter(Boolean);
    if (!clean.length) return;
    captureMany(clean.join("\n"));
    close();
  };

  const appendTrigger = (trigger: string) => {
    setValue((v) => {
      const sep = v.length && !v.endsWith("\n") ? "\n" : "";
      return `${v}${sep}${trigger}: `;
    });
    areaRef.current?.focus();
  };

  return (
    <div
      className="chat-fade-in fixed inset-x-0 bottom-0 top-0 z-[80] flex items-end justify-center bg-black/50 p-0 pt-0 sm:items-start sm:p-4 sm:pt-[10vh]"
      style={vp ? { top: vp.top, height: vp.height, bottom: "auto" } : undefined}
      onClick={close}
    >
      <div
        className="flex max-h-full w-full max-w-xl flex-col overflow-hidden rounded-t-2xl border-t border-border bg-card shadow-2xl pb-safe sm:max-h-[85vh] sm:rounded-2xl sm:border sm:pb-0"
        onClick={(e) => e.stopPropagation()}
      >
        {/* header: mode toggle + close */}
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <div className="flex rounded-lg bg-secondary p-0.5 text-xs">
            <ModeTab active={mode === "single"} onClick={() => switchMode("single")} icon={themedIcon("Plus")}>
              Quick
            </ModeTab>
            <ModeTab active={mode === "sweep"} onClick={() => switchMode("sweep")} icon={themedIcon("Wind")}>
              Mind sweep
            </ModeTab>
          </div>
          <span className="ml-auto text-[11px] text-muted-foreground">
            Capture only — clarify later
          </span>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={close} aria-label="Close" className="rounded-md">
            <AppIcon name="X" className="h-4 w-4" />
          </Button>
        </div>

        {mode === "single" ? (
          <div className="p-4">
            <div className="tech-transition flex items-center gap-3 rounded-xl border border-border bg-background/60 px-4 py-3 focus-within:border-primary/50">
              <AppIcon name="Plus" className="h-5 w-5 shrink-0 text-muted-foreground" />
              <input
                ref={inputRef}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    submitSingle();
                  }
                }}
                autoFocus
                placeholder="Capture to inbox… (# for a project)"
                aria-label="Capture to inbox"
                className="flex-1 bg-transparent text-base text-foreground placeholder:text-muted-foreground focus:outline-none"
              />
              <button
                type="button"
                onClick={submitSingle}
                disabled={!value.trim() || verifying}
                aria-label="Add to inbox"
                className={[
                  "tech-transition inline-flex shrink-0 items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium",
                  value.trim() && !verifying
                    ? "bg-primary text-primary-foreground hover:opacity-90"
                    : "cursor-not-allowed bg-secondary text-muted-foreground",
                ].join(" ")}
              >
                {verifying ? (
                  <>
                    Checking… <AppIcon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
                  </>
                ) : (
                  <>
                    Add <AppIcon name="CornerDownLeft" className="h-3.5 w-3.5" />
                  </>
                )}
              </button>
            </div>
            {(() => {
              // S6g — say where a `#` token files it, before Enter.
              const hit = parseProjectToken(value, targets).match;
              return hit ? (
                <p className="mt-1.5 flex items-center gap-1 px-1 text-[11px] text-primary">
                  <AppIcon name={hit.kind === "area" ? "Lock" : "FolderKanban"} className="h-3 w-3" />
                  Goes to {hit.name}
                </p>
              ) : null;
            })()}
            <AttachmentComposer attachments={pendingAtts} onChange={setPendingAtts} compact />
            <CaptureWhen
              iso={dateIso}
              kind={dateKind}
              hint={detectDateHint(value)}
              onChange={(iso, k) => { setDateIso(iso); setDateKind(k); }}
              onClear={() => setDateIso("")}
            />
            <div className="mt-2 flex items-center justify-between px-1 text-[11px] text-muted-foreground">
              <span>Enter to add · keep going · Esc to close</span>
              {added > 0 && (
                <span className="inline-flex items-center gap-1 text-success">
                  <AppIcon name="Check" className="h-3 w-3" />
                  {added} captured
                </span>
              )}
            </div>
          </div>
        ) : phase === "write" ? (
          <div className="p-4">
            <p className="mb-2 text-xs text-muted-foreground">
              Empty your head — one thought per line. Use the prompts to jog your memory.
            </p>
            <textarea
              ref={areaRef}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              autoFocus
              rows={7}
              placeholder={"Call the lab about calibration\nBook flights for Bangalore\nAsk Priya about the vendor review\n…"}
              className="w-full resize-none rounded-xl border border-border bg-background/60 px-3 py-2.5 text-base leading-relaxed text-foreground placeholder:text-muted-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
            />
            <div className="mt-2.5">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Trigger list
              </p>
              <div className="flex flex-wrap gap-1.5">
                {CAPTURE_TRIGGERS.map((t) => (
                  <button
                    key={t}
                    type="button"
                    onClick={() => appendTrigger(t)}
                    className="tech-transition rounded-full border border-border px-2.5 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:bg-primary/5 hover:text-foreground"
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
            <div className="mt-3 flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">
                {lineCount} item{lineCount === 1 ? "" : "s"}
              </span>
              <button
                type="button"
                disabled={!lineCount}
                onClick={goToReview}
                className={[
                  "tech-transition inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium",
                  lineCount
                    ? "bg-primary text-primary-foreground hover:opacity-90"
                    : "cursor-not-allowed bg-secondary text-muted-foreground",
                ].join(" ")}
              >
                Review {lineCount || ""} item{lineCount === 1 ? "" : "s"}
                <AppIcon name="ArrowRight" className="h-4 w-4" />
              </button>
            </div>
          </div>
        ) : (
          <div className="p-4">
            <div className="mb-2.5 flex items-start gap-1.5 rounded-lg border border-primary/20 bg-primary/5 px-2.5 py-2 text-[11px] text-muted-foreground">
              {atomizing ? (
                <AppIcon name="Loader2" className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
              ) : (
                <AppIcon name="Sparkles" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
              )}
              <span>
                {atomizing
                  ? "AI is splitting your dump into atomic items and checking for duplicates…"
                  : splitFromSingle
                    ? "This looks like several tasks — review and split them, or keep it as one below. Nothing is filed until you confirm."
                    : "Review before adding — edit, remove, or re-include any item. Nothing is filed until you confirm."}
              </span>
            </div>
            <div className="flex max-h-[42vh] flex-col gap-1.5 overflow-y-auto pr-1">
              {reviewItems.map((item, i) => (
                <div key={i} className="flex flex-col gap-0.5">
                  <div className={`flex items-center gap-2 ${item.include ? "" : "opacity-45"}`}>
                    <button
                      type="button"
                      aria-label={item.include ? "Skip this item" : "Include this item"}
                      onClick={() => {
                        reviewEditedRef.current = true;
                        setReviewItems((ls) =>
                          ls.map((l, idx) => (idx === i ? { ...l, include: !l.include } : l)),
                        );
                      }}
                      className={`tech-transition flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded border ${
                        item.include
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border text-transparent"
                      }`}
                    >
                      <AppIcon name="Check" className="h-3 w-3" />
                    </button>
                    <input
                      value={item.title}
                      onChange={(e) => {
                        reviewEditedRef.current = true;
                        setReviewItems((ls) =>
                          ls.map((l, idx) => (idx === i ? { ...l, title: e.target.value } : l)),
                        );
                      }}
                      className="tech-transition flex-1 rounded-md border border-border bg-background/60 px-2.5 py-1.5 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        reviewEditedRef.current = true;
                        setReviewItems((ls) => ls.filter((_, idx) => idx !== i));
                      }}
                      aria-label="Remove item"
                      className="tech-transition rounded-md p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    >
                      <AppIcon name="Trash2" className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  {item.verdict !== "new" && (
                    <div className="ml-6 flex items-center gap-1 text-[10px]">
                      <AppIcon name="CopyX" className={`h-3 w-3 ${item.verdict === "duplicate" ? "text-warning" : "text-muted-foreground"}`} />
                      <span className={item.verdict === "duplicate" ? "text-warning" : "text-muted-foreground"}>
                        {item.verdict === "duplicate"
                          ? `Already ${matchWhere(item.matchDisposition, item.matchSource)}: "${item.matchTitle}" — skipped (tap the box to add anyway)`
                          : `Similar to ${matchWhere(item.matchDisposition, item.matchSource)}: "${item.matchTitle}" — same item? Untick to skip.`}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={() => {
                reviewEditedRef.current = true;
                setReviewItems((ls) => [...ls, { title: "", include: true, verdict: "new" }]);
              }}
              className="tech-transition mt-2 inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
            >
              <AppIcon name="Plus" className="h-3 w-3" /> Add another
            </button>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPhase("write")}
                  className="tech-transition inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
                >
                  <AppIcon name="ArrowLeft" className="h-4 w-4" /> Back
                </button>
                {splitFromSingle && (
                  <button
                    type="button"
                    onClick={() => { doCaptureSingle(value.trim()); close(); }}
                    title="File the original text as a single inbox item instead"
                    className="tech-transition inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
                  >
                    Keep as one
                  </button>
                )}
              </div>
              <button
                type="button"
                disabled={!reviewCount}
                onClick={commitReview}
                className={[
                  "tech-transition inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium",
                  reviewCount
                    ? "bg-primary text-primary-foreground hover:opacity-90"
                    : "cursor-not-allowed bg-secondary text-muted-foreground",
                ].join(" ")}
              >
                <AppIcon name="ListPlus" className="h-4 w-4" />
                Add {reviewCount} to inbox
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ModeTab({
  active,
  onClick,
  icon: Icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: ThemedIcon;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "tech-transition inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium",
        active
          ? "bg-card text-foreground shadow-sm"
          : "text-muted-foreground hover:text-foreground",
      ].join(" ")}
    >
      <Icon className="h-3.5 w-3.5" />
      {children}
    </button>
  );
}

// Optional capture-time date (GTD tickler / deadline). "Remind me" sets a
// defer_until so the item is hidden until then and resurfaces in the inbox;
// "Due date" sets a hard due_at that shows on the Calendar. Capture stays fast:
// this is tucked below the input and never required. A detected date phrase in
// the title surfaces a one-tap hint.
function CaptureWhen({
  iso,
  kind,
  hint,
  onChange,
  onClear,
}: {
  iso: string;
  kind: "remind" | "due";
  hint: string | null;
  onChange: (iso: string, kind: "remind" | "due") => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const opts = snoozeOptions();
  const dateLabel = iso
    ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : "";

  if (iso && !open) {
    return (
      <div className="mt-2 flex items-center gap-2 px-1">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/40 bg-primary/10 px-2.5 py-1 text-[11px] font-medium text-primary">
          {kind === "remind" ? <AppIcon name="Bell" className="h-3 w-3" /> : <AppIcon name="CalendarClock" className="h-3 w-3" />}
          {kind === "remind" ? "Remind" : "Due"} {dateLabel}
        </span>
        <Button variant="text" size="none" layout="" type="button" onClick={() => setOpen(true)} className="text-[11px]">
          Change
        </Button>
        <button type="button" onClick={onClear} className="text-[11px] text-muted-foreground hover:text-destructive">
          Clear
        </button>
      </div>
    );
  }

  if (!open) {
    return (
      <div className="mt-2 flex items-center gap-2 px-1">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="tech-transition inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-muted-foreground hover:border-primary/40 hover:text-foreground"
        >
          <AppIcon name="Bell" className="h-3 w-3" /> Remind me / due date
        </button>
        {hint && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10.5px] font-medium text-primary"
            title="Set a date for this capture"
          >
            <AppIcon name="Sparkles" className="h-3 w-3" /> “{hint}” — set a date?
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mt-2 flex flex-col gap-2 rounded-lg border border-border bg-background/50 p-2.5">
      <div className="flex items-center gap-1.5">
        {(["remind", "due"] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => onChange(iso, k)}
            className={[
              "tech-transition inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium",
              kind === k
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:bg-secondary",
            ].join(" ")}
          >
            {k === "remind" ? <AppIcon name="Bell" className="h-3 w-3" /> : <AppIcon name="CalendarClock" className="h-3 w-3" />}
            {k === "remind" ? "Remind me" : "Due date"}
          </button>
        ))}
        <Button variant="text" size="none" layout="" type="button" onClick={() => setOpen(false)} className="ml-auto text-[11px]">
          Done
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {opts.map((o) => (
          <button
            key={o.label}
            type="button"
            onClick={() => onChange(o.iso, kind)}
            className={[
              "tech-transition rounded-full border px-2.5 py-1 text-[11px]",
              iso === o.iso
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:bg-secondary",
            ].join(" ")}
          >
            {o.label}
          </button>
        ))}
        <input
          type="date"
          value={iso ? iso.slice(0, 10) : ""}
          onChange={(e) => {
            if (!e.target.value) return onClear();
            const d = new Date(e.target.value);
            d.setHours(9, 0, 0, 0);
            onChange(d.toISOString(), kind);
          }}
          className="rounded-md border border-border bg-background/60 px-2 py-1 text-[11px] text-foreground focus:border-primary/50 focus:outline-none"
        />
      </div>
      <p className="text-[10px] text-muted-foreground">
        {kind === "remind"
          ? "Hidden from the inbox until this date, then it resurfaces."
          : "A deadline — shows on the Calendar and flags when it's due."}
      </p>
    </div>
  );
}
