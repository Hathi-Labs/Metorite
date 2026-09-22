"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useCallback, useEffect, useState } from "react";
import {
  createEmailCapture, createReplyCommitment, detectReplyCommitment,
  enhanceEmailCapture, previewEmailCapture,
  type AlreadyCapturedTask, type SimilarTask, type TaskCaptureDraft,
} from "../lib/api";

/**
 * The reply that just committed the user to a task. When the modal is opened
 * with this instead of an emailId, it runs in "commitment" mode: it's keyed on
 * the thread + reply body (the sent message isn't mirrored locally yet), the
 * draft is pre-fetched by the detector on send (so no preview round-trip), and
 * "Enhance with AI" re-runs the commitment detector rather than the inbound
 * capture LLM.
 */
export interface CommitmentContext {
  threadId: string;
  body: string;
  subject: string;
  replyToMessageId?: string | null;
  /** The routed draft the detector already produced on send. */
  draft: TaskCaptureDraft;
  similar: SimilarTask[];
}

/**
 * "Add to Tasks" clarify-before-capture popup.
 *
 * Opens immediately with a programmatic default title (derived from the email
 * subject) so nothing blocks on the LLM. The user can edit every field by
 * hand, or hit "Enhance with AI" to have the assistant read the whole email +
 * thread and fill in a proper title/description, disposition, due date and
 * delegate. Similar existing tasks (same thread, or a fuzzily-matching title)
 * are surfaced up-front so the user doesn't create a duplicate. Nothing is
 * written until "Add task" is confirmed.
 */

// Capture-time dispositions the backend accepts (_CAPTURE_DISPOSITIONS).
const DISPOSITIONS: { value: string; label: string; hint: string }[] = [
  { value: "INBOX", label: "Inbox", hint: "Clarify later" },
  { value: "NEXT", label: "Next action", hint: "I'll do this" },
  { value: "WAITING", label: "Waiting / delegate", hint: "On someone else" },
  { value: "CALENDAR", label: "Scheduled", hint: "Date-specific" },
  { value: "SOMEDAY", label: "Someday", hint: "No action now" },
];

const CONTEXTS = ["", "@computer", "@calls", "@errands", "@office", "@home", "@agenda"];

const ENERGIES: { value: string; label: string }[] = [
  { value: "", label: "—" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

function toDateInput(iso: string): string {
  // The draft carries ISO strings (possibly with time); the <input type=date>
  // wants YYYY-MM-DD. Keep just the date part; empty stays empty.
  if (!iso) return "";
  const m = iso.match(/^\d{4}-\d{2}-\d{2}/);
  return m ? m[0] : "";
}

export function TaskCaptureModal({
  accountId,
  emailId,
  commitment,
  onClose,
  onCaptured,
}: {
  accountId: string;
  /** Inbound capture: the message to capture. Omit in commitment mode. */
  emailId?: string;
  /** Commitment capture: the just-sent reply + its pre-fetched draft. */
  commitment?: CommitmentContext;
  onClose: () => void;
  onCaptured: (notice: {
    title: string;
    created: boolean;
    disposition?: string;
    assigneeName?: string | null;
    dueAt?: string | null;
  }) => void;
}) {
  // Commitment mode arrives with its draft already detected on send, so nothing
  // blocks on a preview round-trip.
  const [loading, setLoading] = useState(!commitment);
  const [enhancing, setEnhancing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [enhanced, setEnhanced] = useState(false);

  const [draft, setDraft] = useState<TaskCaptureDraft | null>(
    commitment?.draft ?? null);
  const [similar, setSimilar] = useState<SimilarTask[]>(
    commitment?.similar ?? []);
  const [already, setAlready] = useState<AlreadyCapturedTask | null>(null);

  // Load the preview (default title + similar tasks) when the popup opens.
  // Commitment mode skips this — its draft was detected on send.
  useEffect(() => {
    if (commitment || !emailId) return;
    let cancelled = false;
    setLoading(true);
    setErr(null);
    previewEmailCapture(accountId, emailId)
      .then((p) => {
        if (cancelled) return;
        setDraft(p.draft);
        setSimilar(p.similar);
        setAlready(p.alreadyCaptured);
      })
      .catch((e) => {
        if (!cancelled) setErr((e as Error).message || "Couldn't prepare the task.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [accountId, emailId, commitment]);

  // Close on Escape (unless mid-save).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  const patch = useCallback(
    (u: Partial<TaskCaptureDraft>) => setDraft((d) => (d ? { ...d, ...u } : d)),
    []
  );

  const handleEnhance = async () => {
    setEnhancing(true);
    setErr(null);
    try {
      // Commitment mode re-runs the detector over the sent reply; inbound mode
      // runs the capture LLM over the source email. Both return a routed draft.
      const res = commitment
        ? await detectReplyCommitment({
            accountId,
            threadId: commitment.threadId,
            body: commitment.body,
            subject: commitment.subject,
            replyToMessageId: commitment.replyToMessageId,
            // Enhance reviews the WHOLE conversation (the send-time detect ran
            // fast on the reply body alone).
            includeThread: true,
          }).then((d) => ({
            draft: d.draft ?? draft!,
            usedLlm: d.isCommitment,
          }))
        : await enhanceEmailCapture(accountId, emailId!);
      // Merge over the current draft: the AI supplies the richer fields, but
      // if the user had already typed a title we still overwrite it (that's
      // the point of Enhance) — they can undo by editing again.
      setDraft(res.draft);
      setEnhanced(res.usedLlm);
      if (!res.usedLlm) {
        setErr("AI is unavailable right now — you can still edit and add the task.");
      }
    } catch (e) {
      setErr((e as Error).message || "Enhance failed.");
    } finally {
      setEnhancing(false);
    }
  };

  const handleSave = async () => {
    if (!draft || !draft.title.trim()) return;
    setSaving(true);
    setErr(null);
    try {
      const res = commitment
        ? await createReplyCommitment({
            accountId,
            threadId: commitment.threadId,
            subject: commitment.subject,
            body: commitment.body,
            replyToMessageId: commitment.replyToMessageId,
            draft,
          })
        : await createEmailCapture(accountId, emailId!, draft);
      onCaptured({
        title: res.title,
        created: res.created,
        disposition: res.disposition,
        assigneeName: res.assigneeName,
        dueAt: res.dueAt,
      });
      onClose();
    } catch (e) {
      setErr((e as Error).message || "Couldn't add the task.");
      setSaving(false);
    }
  };

  const disp = draft?.disposition || "INBOX";
  const showAssignee = disp === "WAITING";
  const showDue = disp === "NEXT" || disp === "CALENDAR" || disp === "WAITING";
  // For an actionable capture (I'll do this), the AI also clarifies the energy,
  // time estimate and any subtasks — mirror the inbox Clarify flow so the task
  // lands complete. These fields are meaningless for WAITING/SOMEDAY/Inbox.
  const showDetails = disp === "NEXT" || disp === "CALENDAR";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4 bg-black/40"
      onClick={() => !saving && onClose()}
    >
      <div
        className="bg-card border border-border rounded-xl shadow-2xl w-full max-w-lg max-h-[88vh] flex flex-col"
        onClick={(ev) => ev.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-border flex-shrink-0">
          <div className="text-sm font-medium text-foreground">
            {commitment ? "You committed to a task" : "Add to My Tasks"}
          </div>
          <button
            onClick={onClose}
            disabled={saving}
            className="text-muted-foreground hover:text-foreground flex-shrink-0 disabled:opacity-40"
            title="Close (Esc)"
          >
            <Icon name="X" size={16} />
          </button>
        </div>

        <div className="overflow-y-auto px-4 py-3 flex-1 space-y-3">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground py-8 justify-center">
              <Icon name="Loader2" className="animate-spin" size={14} /> Preparing task…
            </div>
          ) : (
            <>
              {/* Already captured — offer to open instead of duplicating. */}
              {already && (
                <div className="flex items-start gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
                  <Icon name="CheckCircle2" size={14} className="text-primary mt-0.5 flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="text-foreground">
                      This email is already in My Tasks: &ldquo;{already.title}&rdquo;
                    </div>
                    <a href="/tasks" className="text-primary font-medium hover:opacity-80">
                      Open My Tasks →
                    </a>
                  </div>
                </div>
              )}

              {/* Similar-task warning. */}
              {similar.length > 0 && (
                <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs">
                  <div className="flex items-center gap-1.5 text-amber-600 dark:text-amber-500 font-medium mb-1">
                    <Icon name="AlertTriangle" size={13} /> You may already have this
                  </div>
                  <ul className="space-y-0.5">
                    {similar.map((s) => (
                      <li key={s.id} className="flex items-center gap-1.5 text-muted-foreground">
                        <span className="truncate">{s.title}</span>
                        <span className="text-[10px] text-muted-foreground/60 flex-shrink-0">
                          {s.reason === "same-thread" ? "same thread" : "similar"}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Title */}
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                  Task
                </label>
                <input
                  value={draft?.title ?? ""}
                  onChange={(e) => patch({ title: e.target.value })}
                  placeholder="What needs to happen?"
                  autoFocus
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary/50 focus:outline-none"
                />
              </div>

              {/* Enhance with AI */}
              <button
                type="button"
                onClick={handleEnhance}
                disabled={enhancing}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-primary transition-colors disabled:opacity-50"
              >
                {enhancing ? (
                  <Icon name="Loader2" size={13} className="animate-spin" />
                ) : (
                  <Icon name="Sparkles" size={13} />
                )}
                {enhancing
                  ? commitment
                    ? "Reading the conversation…"
                    : "Reading the email…"
                  : enhanced
                  ? "Re-enhance with AI"
                  : commitment
                  ? "Review full conversation with AI"
                  : "Enhance with AI"}
              </button>
              {enhanced && (
                <p className="text-[10px] text-muted-foreground/70 -mt-1">
                  AI read the full {commitment ? "conversation" : "email & thread"}{" "}
                  to name and route this task.
                </p>
              )}

              {/* Notes / description */}
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                  Notes
                </label>
                <textarea
                  value={draft?.notes ?? ""}
                  onChange={(e) => patch({ notes: e.target.value })}
                  rows={2}
                  placeholder="Context — who wants what, any deadline"
                  className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary/50 focus:outline-none resize-none"
                />
              </div>

              {/* Disposition */}
              <div>
                <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                  Type
                </label>
                <div className="flex flex-wrap gap-1.5">
                  {DISPOSITIONS.map((d) => (
                    <button
                      key={d.value}
                      type="button"
                      onClick={() => patch({ disposition: d.value })}
                      title={d.hint}
                      className={`rounded-full px-2.5 py-1 text-xs border transition-colors ${
                        disp === d.value
                          ? "border-primary bg-primary/10 text-primary font-medium"
                          : "border-border text-muted-foreground hover:border-primary/40"
                      }`}
                    >
                      {d.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Row: assignee (waiting) + due date + context */}
              <div className="grid grid-cols-2 gap-3">
                {showAssignee && (
                  <div className="col-span-2">
                    <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                      Waiting on / delegate to
                    </label>
                    <input
                      value={draft?.assigneeName ?? ""}
                      onChange={(e) => patch({ assigneeName: e.target.value })}
                      placeholder="Name (a teammate on record → delegated to their PM tool)"
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary/50 focus:outline-none"
                    />
                  </div>
                )}
                {showDue && (
                  <div>
                    <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                      {disp === "WAITING" ? "Expected by" : "Due"}
                    </label>
                    <input
                      type="date"
                      value={toDateInput(draft?.dueAt ?? "")}
                      onChange={(e) => patch({ dueAt: e.target.value })}
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                    />
                  </div>
                )}
                <div className={showDue ? "" : "col-span-2"}>
                  <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                    Context
                  </label>
                  <select
                    value={draft?.context ?? ""}
                    onChange={(e) => patch({ context: e.target.value })}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                  >
                    {CONTEXTS.map((c) => (
                      <option key={c} value={c}>
                        {c || "None"}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Actionable-task details — energy, estimate, subtasks. The AI
                  fills these when it routes the task to a next action so it
                  lands complete (clarify parity); the user can tweak them. */}
              {showDetails && (
                <div className="space-y-3 rounded-lg border border-border/60 bg-secondary/20 px-3 py-2.5">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                        Energy
                      </label>
                      <select
                        value={draft?.energy ?? ""}
                        onChange={(e) => patch({ energy: e.target.value })}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-primary/50 focus:outline-none"
                      >
                        {ENERGIES.map((en) => (
                          <option key={en.value} value={en.value}>
                            {en.label}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                        Estimate (min)
                      </label>
                      <input
                        type="number"
                        min={0}
                        value={draft?.timeEstimateMins ?? ""}
                        onChange={(e) =>
                          patch({
                            timeEstimateMins:
                              e.target.value === ""
                                ? null
                                : Math.max(0, Number(e.target.value)),
                          })
                        }
                        placeholder="—"
                        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary/50 focus:outline-none"
                      />
                    </div>
                  </div>
                  {(draft?.subtasks.length ?? 0) > 0 && (
                    <div>
                      <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                        Steps ({draft?.subtasks.length}) — created as subtasks
                      </label>
                      <ul className="space-y-1">
                        {draft?.subtasks.map((s, i) => (
                          <li key={i} className="flex items-center gap-1.5">
                            <span className="text-[10px] text-muted-foreground/60 w-4 flex-shrink-0 text-right">
                              {i + 1}.
                            </span>
                            <input
                              value={s}
                              onChange={(e) =>
                                patch({
                                  subtasks: (draft?.subtasks ?? []).map((x, j) =>
                                    j === i ? e.target.value : x
                                  ),
                                })
                              }
                              className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground focus:border-primary/50 focus:outline-none"
                            />
                            <button
                              type="button"
                              onClick={() =>
                                patch({
                                  subtasks: (draft?.subtasks ?? []).filter(
                                    (_, j) => j !== i
                                  ),
                                })
                              }
                              className="flex-shrink-0 text-muted-foreground/50 hover:text-destructive"
                              title="Remove step"
                            >
                              <Icon name="X" size={12} />
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {err && <div className="text-xs text-destructive">{err}</div>}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-border flex-shrink-0">
          <Button variant="text" size="none" layout="" onClick={onClose} disabled={saving} className="px-3 py-1.5 text-xs">
            Cancel
          </Button>
          <Button layout="inline-flex items-center" onClick={handleSave} disabled={loading || saving || !draft?.title.trim()}>
            {saving ? (
              <Icon name="Loader2" size={13} className="animate-spin" />
            ) : (
              <Icon name="ArrowRight" size={13} />
            )}
            {already ? "Add anyway" : "Add task"}
          </Button>
        </div>
      </div>
    </div>
  );
}
