"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useRef, useState } from "react";
import type { DraftStep, DraftRevision } from "../lib/useDraftSession";

/**
 * The composer's AI drafting panel.
 *
 * Drafting is a conversation, not a button: the panel stays open after a draft
 * lands so the next instruction refines it, keeps each round as a revision the
 * user can flip back to, and shows what the backend is actually doing —
 * which specialist was asked, the question put to it, what the model is
 * thinking — instead of a spinner.
 */

function StepIcon({ state }: { state: DraftStep["state"] }) {
  if (state === "running") {
    return (
      <Icon name="Loader2"
        size={11}
        className="text-primary animate-spin flex-shrink-0 mt-[3px]"
        aria-hidden
      />
    );
  }
  if (state === "failed") {
    return (
      <Icon name="AlertTriangle"
        size={11}
        className="text-warning flex-shrink-0 mt-[3px]"
        aria-hidden
      />
    );
  }
  return (
    <Icon name="Check" size={11} className="text-success flex-shrink-0 mt-[3px]" aria-hidden />
  );
}

function StepRow({ step }: { step: DraftStep }) {
  return (
    <li className="chat-fade-in">
      <div className="flex items-start gap-2">
        <StepIcon state={step.state} />
        <span className="text-[11px] text-foreground/90 leading-[1.35]">
          {step.label}
          {step.detail && (
            <span className="text-muted-foreground"> — {step.detail}</span>
          )}
        </span>
      </div>
      {/* The actual question put to the specialist agent: the point of the
          panel is that this is visible, not buried in a server log. */}
      {step.question && (
        <p className="ml-[19px] mt-0.5 text-[10.5px] italic text-muted-foreground leading-snug">
          “{step.question}”
        </p>
      )}
      {step.answer && (
        <p className="ml-[19px] mt-0.5 text-[10.5px] text-muted-foreground/90 leading-snug line-clamp-3">
          {step.answer}
        </p>
      )}
    </li>
  );
}

function ThinkingBox({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Follow the model's reasoning as it streams.
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [text]);
  return (
    <div className="ml-[19px] mt-1">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70 mb-0.5">
        Thinking
      </div>
      {/* Height is an exact multiple of the line box (4 × 14px) AND the
          scroller carries no vertical padding, so scrolled-to-bottom always
          lands on a line boundary. Any vertical padding here re-introduces a
          half-cut top line, which reads as a rendering bug. */}
      <div
        ref={ref}
        className="max-h-[56px] overflow-y-auto scrollbar-thin rounded-md bg-secondary/50 px-2 font-mono text-[10px] leading-[14px] text-muted-foreground whitespace-pre-wrap break-words"
      >
        {text}
      </div>
    </div>
  );
}

function RevisionChips({
  revisions,
  activeId,
  disabled,
  onRestore,
}: {
  revisions: DraftRevision[];
  activeId: number | null;
  disabled: boolean;
  onRestore: (id: number) => void;
}) {
  if (revisions.length < 2) return null;
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {revisions.map((rev) => {
        const active = rev.id === activeId;
        return (
          <button
            key={rev.id}
            type="button"
            disabled={disabled}
            onClick={() => onRestore(rev.id)}
            title={
              rev.instruction
                ? `Restore ${rev.label} — “${rev.instruction}”`
                : rev.source === "user"
                  ? "Restore your original text"
                  : `Restore ${rev.label}`
            }
            aria-pressed={active}
            className={`px-1.5 py-[1px] rounded text-[10px] tech-transition disabled:opacity-50 ${
              active
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary"
            }`}
          >
            {rev.label}
          </button>
        );
      })}
    </div>
  );
}

export interface DraftAssistantProps {
  instruction: string;
  onInstruction: (v: string) => void;
  busy: boolean;
  /** The composer body already has text → the run edits it rather than starts one. */
  hasText: boolean;
  /** At least one AI round has landed → this is now a refine loop. */
  hasDraft: boolean;
  steps: DraftStep[];
  thinking: string;
  revisions: DraftRevision[];
  activeRevision: number | null;
  elapsedMs: number | null;
  onRun: () => void;
  onRestore: (id: number) => void;
  onClose: () => void;
}

export function DraftAssistant({
  instruction,
  onInstruction,
  busy,
  hasText,
  hasDraft,
  steps,
  thinking,
  revisions,
  activeRevision,
  elapsedMs,
  onRun,
  onRestore,
  onClose,
}: DraftAssistantProps) {
  // The feed is open while working and collapses to a summary once settled —
  // the draft is what you read afterwards, not the trace. Derived from `busy`
  // rather than synced in an effect; an explicit toggle overrides it until the
  // next run (which clears the override in onRunClick below).
  const [manualExpand, setManualExpand] = useState<boolean | null>(null);
  const expanded = manualExpand ?? busy;
  const inputRef = useRef<HTMLInputElement>(null);
  const wasBusy = useRef(false);

  // Hand focus back to the instruction box when a round finishes, so the next
  // refinement is one keystroke away. DOM-only: no state sync here.
  useEffect(() => {
    if (wasBusy.current && !busy) inputRef.current?.focus();
    wasBusy.current = busy;
  }, [busy]);

  const onRunClick = () => {
    setManualExpand(null); // a new round re-opens the feed
    onRun();
  };

  const doneSteps = steps.filter((s) => s.state !== "running");
  const consulted = steps
    .filter((s) => s.key.startsWith("consult:") && s.state === "done")
    .map((s) => s.label.replace(/^Asked /, ""));
  const summary = [
    elapsedMs != null ? `Drafted in ${(elapsedMs / 1000).toFixed(1)}s` : null,
    consulted.length ? `asked ${consulted.join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const showHeader = busy || hasDraft || steps.length > 0;
  const runLabel = hasDraft ? "Refine" : hasText ? "Improve" : "Draft";

  // `relative` holds the `sr-only` summary below. `sr-only` is
  // `position: absolute`, and without a positioned ancestor it escaped every
  // clip box. It sat in the long thread and made the document taller than the
  // window, so a wheel moved the whole page (owner report, 2026-10-10).
  return (
    <div className="relative border-t border-border bg-primary/[0.04]">
      {showHeader && (
        <div className="flex items-center gap-2 px-4 pt-2">
          <span className="text-[10px] font-medium uppercase tracking-wide text-primary">
            {busy ? (hasDraft ? "Refining" : "Drafting") : "AI draft"}
          </span>
          {hasDraft && revisions.length > 1 && (
            <>
              <span className="text-muted-foreground/40" aria-hidden>
                ·
              </span>
              <RevisionChips
                revisions={revisions}
                activeId={activeRevision}
                disabled={busy}
                onRestore={onRestore}
              />
            </>
          )}
          <div className="ml-auto flex items-center gap-1">
            {!busy && summary && !expanded && (
              <span className="text-[10px] text-muted-foreground truncate max-w-[16rem]">
                {summary}
              </span>
            )}
            {steps.length > 0 && (
              <Button variant="ghost" size="none" radius="keep" layout="" type="button" onClick={() => setManualExpand(!expanded)} title={expanded ? "Hide activity" : "Show what the AI did"} aria-expanded={expanded} className="p-0.5 rounded">
                {expanded ? <Icon name="ChevronUp" size={13} /> : <Icon name="ChevronDown" size={13} />}
              </Button>
            )}
          </div>
        </div>
      )}

      {expanded && steps.length > 0 && (
        <ol
          className="px-4 pt-1.5 pb-0.5 space-y-1 max-h-44 overflow-y-auto scrollbar-thin"
          aria-live="polite"
          aria-busy={busy}
        >
          {steps.map((step) => (
            <StepRow key={step.key} step={step} />
          ))}
          {thinking && busy && <ThinkingBox text={thinking} />}
        </ol>
      )}

      {/* Refine affordance: after a draft lands, say plainly that another
          instruction edits it — the loop is the feature, not a hidden trick. */}
      {hasDraft && !busy && (
        <p className="px-4 pt-1.5 text-[10.5px] text-muted-foreground">
          Keep going — tell it what to change and it edits this draft.
          {revisions.length > 1 && " Earlier versions stay one click away."}
        </p>
      )}

      <div className="px-4 py-2 flex items-center gap-2">
        <Icon name="Sparkles" size={13} className="text-primary flex-shrink-0" aria-hidden />
        <input
          ref={inputRef}
          type="text"
          value={instruction}
          autoFocus
          disabled={busy}
          onChange={(e) => onInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (!busy) onRunClick();
            } else if (e.key === "Escape") {
              onClose();
            }
          }}
          placeholder={
            hasDraft
              ? "What should change? (e.g. shorter, add the deadline)"
              : hasText
                ? "How should AI improve this? (optional)"
                : "What should this email say? (optional)"
          }
          aria-label={hasDraft ? "Refine the draft" : "Draft with AI"}
          className="flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground outline-none min-w-0 disabled:opacity-60"
        />
        {hasDraft && revisions.length > 1 && !busy && (
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={() => {
              const prev = revisions[revisions.length - 2];
              if (prev) onRestore(prev.id);
            }} title="Go back to the previous version" className="rounded flex-shrink-0">
            <Icon name="Undo2" size={13} />
          </Button>
        )}
        <Button size="none" radius="keep" layout="flex items-center" type="button" onClick={onRunClick} disabled={busy} className="px-2.5 py-1 text-xs rounded-md gap-1 flex-shrink-0">
          {busy ? (
            <Icon name="Loader2" size={12} className="animate-spin" aria-hidden />
          ) : (
            <Icon name="CornerDownLeft" size={12} aria-hidden />
          )}
          {runLabel}
        </Button>
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          className="text-muted-foreground hover:text-foreground tech-transition flex-shrink-0 disabled:opacity-50"
          title="Close"
          aria-label="Close AI drafting"
        >
          <Icon name="X" size={14} />
        </button>
      </div>
      {/* Keeps the collapsed summary reachable for screen readers. */}
      {!expanded && doneSteps.length > 0 && (
        <span className="sr-only">
          {doneSteps.map((s) => `${s.label} ${s.detail}`).join(". ")}
        </span>
      )}
    </div>
  );
}
