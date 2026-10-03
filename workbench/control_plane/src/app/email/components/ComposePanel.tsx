"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useState, useEffect, useRef } from "react";
import { useEmailStore } from "../lib/emailStore";
import {
  fileToSendAttachment, getSentFrom,
  type SendAttachment, type ArtifactAttachmentRef,
} from "../lib/api";
import { fromWarning, needsReconnect, swapSignature } from "../lib/mailbox";
import { FromRow } from "./FromRow";
import { mailboxLabel } from "./MailboxChip";
import { useDraftSession } from "../lib/useDraftSession";
import { DraftAssistant } from "./DraftAssistant";
import { splitQuotedText } from "../lib/quoting";
import { appendSignature, getSignatureText, stripSignature } from "../lib/signature";
import { ArtifactAttachPicker } from "./ArtifactAttachPicker";
import { RecipientInput } from "./RecipientInput";
import { ComposerQuote, AiButton } from "./ComposerAI";

interface ComposePanelProps {
  open: boolean;
  onClose: () => void;
  /** The mailbox the composer opens on: the mailbox of the mail a reply
   *  answers, else the selected one. The From row can change it (EM-T8c). */
  accountId: string;
  onSend: (params: {
    /** The mailbox that sends: the choice of the From row. */
    accountId: string;
    to: string[];
    cc?: string[];
    bcc?: string[];
    subject: string;
    bodyText: string;
    replyToMessageId?: string;
    attachments?: SendAttachment[];
    artifacts?: ArtifactAttachmentRef[];
  }) => Promise<void>;
  defaultTo?: string;
  defaultSubject?: string;
  /** Seeds the Cc field (e.g. restored on an undo-send reopen). */
  defaultCc?: string;
  /** Seeds the editable body (e.g. text carried over from a popped-out reply). */
  replyToBody?: string;
  /** The quoted trailing chain — shown collapsed below the box, reattached on
   *  send, and kept OUT of the editable body so AI/edits never touch it. */
  quote?: string;
  replyToMessageId?: string;
  /** LOCAL id of the message being replied to — lets "Draft with AI" load the
   *  same reply context the inline reply had (compose-assist keys off it). */
  messageId?: string;
  /** Seed attachments/artifacts (e.g. restored on an undo-send reopen) so the
   *  user doesn't silently lose what they'd attached before undoing. */
  initialAttachments?: SendAttachment[];
  initialArtifacts?: ArtifactAttachmentRef[];
}

export function ComposePanel({
  open,
  onClose,
  accountId,
  onSend,
  defaultTo = "",
  defaultSubject = "",
  defaultCc = "",
  replyToBody,
  quote,
  replyToMessageId,
  messageId,
  initialAttachments,
  initialArtifacts,
}: ComposePanelProps) {
  const [to, setTo] = useState(defaultTo);
  const [cc, setCc] = useState(defaultCc);
  const [subject, setSubject] = useState(defaultSubject);
  const [body, setBody] = useState(replyToBody || "");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  // AI draft/refine panel (sparkles button in the footer). The session owns
  // the live backend steps and the per-round revision history.
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInstruction, setAiInstruction] = useState("");
  const { saveDraft, sendDraft, deleteEmail, accounts, authErrors } = useEmailStore();
  // ── The sending mailbox (EM-T8c, D-EM-20) ──
  // The pick of the From row holds only while the composer opens on the same
  // mailbox, so a new compose session starts on its own mailbox.
  const [fromPick, setFromPick] = useState<{ base: string; id: string } | null>(null);
  const fromId = fromPick && fromPick.base === accountId ? fromPick.id : accountId;
  // A reply answers a mail of `accountId`. From any other mailbox it goes as
  // new mail: the provider cannot answer a mail of another mailbox, and the
  // gateway refuses it (MB-6).
  const conversationId = replyToMessageId ? accountId : null;
  const sameConversation = fromId === accountId;
  const replyTarget = sameConversation ? replyToMessageId : undefined;
  const replyContextId = sameConversation ? messageId : undefined;
  // A draft of the old mailbox, deleted once the new mailbox saved its own.
  const staleDraftRef = useRef<string | null>(null);
  // For each recipient, the mailbox that wrote to them last.
  const [usualSender, setUsualSender] = useState<Record<string, string>>({});
  // Gmail-style auto-save: the composed message persists as a Drafts row as you
  // type (draftIdRef holds the local id so repeated saves update it in place).
  const draftIdRef = useRef<string | null>(null);
  const dirty = useRef(false);
  const [draftStatus, setDraftStatus] = useState<"idle" | "saving" | "saved">("idle");
  // Uploaded files (base64) + picked AI artifacts (resolved server-side).
  const [attachments, setAttachments] = useState<SendAttachment[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactAttachmentRef[]>([]);
  const ai = useDraftSession({
    onBody: (next) => {
      dirty.current = true;
      setBody(next);
    },
    onError: setSendError,
  });

  // The signature's plain text — it lives IN the editable body (seeded below),
  // not a separate card, so the auto-saved draft carries it upstream too.
  const [sigText, setSigText] = useState("");
  useEffect(() => {
    let alive = true;
    void getSignatureText(fromId).then((s) => {
      if (alive) setSigText(s);
    });
    return () => {
      alive = false;
    };
  }, [fromId]);

  // A fresh compose session each time the window opens: re-sync fields from the
  // (possibly new) props and forget any prior draft so we don't update it.
  useEffect(() => {
    if (!open) return;
    setTo(defaultTo);
    setCc(defaultCc);
    setSubject(defaultSubject);
    setBody(replyToBody || "");
    // Seed the signature into the body (idempotent — a popped-out reply or an
    // undo-send reopen may already carry it). Async: never clobber typing.
    void getSignatureText(accountId).then((sig) => {
      if (sig) setBody((prev) => appendSignature(prev, sig));
    });
    draftIdRef.current = null;
    staleDraftRef.current = null;
    setFromPick(null);
    dirty.current = false;
    setDraftStatus("idle");
    // Restore any carried attachments/artifacts (undo-send reopen); a fresh
    // compose passes none, so this stays empty as before.
    setAttachments(initialAttachments ?? []);
    setArtifacts(initialArtifacts ?? []);
    setAiOpen(false);
    setAiInstruction("");
    ai.reset(); // a new compose window starts a fresh drafting session
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /** The full outgoing body: the editable text plus the quoted trailing chain. */
  const combinedBody = () =>
    quote ? `${body.replace(/\s+$/, "")}\n\n${quote}` : body;

  /** Draft or refine with AI. Operates only on the NEW text — any inline quote
   *  in the body is split off (and the separate `quote` prop is never sent) so
   *  the AI never rewrites the trailing email. Each run refines the CURRENT
   *  text, so the panel stays open for as many rounds as the user wants. */
  const runAi = async () => {
    if (!fromId || ai.busy) return;
    setSendError(null);
    const { main, quoted } = splitQuotedText(body);
    const toArr = to.split(",").map((s) => s.trim()).filter(Boolean);
    const instruction = aiInstruction.trim();
    const draft = await ai.run(
      {
        accountId: fromId,
        body: main,
        instruction,
        mode: replyTarget ? "reply" : "new",
        // The local message id lets the drafter load the replied-to thread +
        // direction context (parity with the inline reply's "Draft with AI").
        messageId: replyContextId,
        to: toArr,
        subject,
      },
      { instruction, quote: quoted },
    );
    // The bar stays open on success: the next instruction refines this draft.
    if (draft) setAiInstruction("");
  };

  /** Read picked files into base64 and append them to the attachments. */
  const addFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    try {
      const added = await Promise.all(Array.from(files).map(fileToSendAttachment));
      setAttachments((prev) => [...prev, ...added]);
    } catch {
      setSendError("Couldn't read one of the attachments");
    }
  };

  // Debounced auto-save once the user edits the draft.
  useEffect(() => {
    if (!open || !fromId || !dirty.current) return;
    const toArr = to.split(",").map((s) => s.trim()).filter(Boolean);
    if (!body.trim() && toArr.length === 0 && !subject.trim()) return;
    const handle = setTimeout(async () => {
      try {
        setDraftStatus("saving");
        const saved = await saveDraft({
          accountId: fromId,
          draftId: draftIdRef.current ?? undefined,
          replyToMessageId: draftIdRef.current ? undefined : (replyTarget || undefined),
          to: toArr,
          cc: cc ? cc.split(",").map((s) => s.trim()).filter(Boolean) : [],
          subject,
          body: combinedBody(),
        });
        draftIdRef.current = saved.id;
        // A provider draft cannot move between mailboxes: the new mailbox
        // saved its own, so the draft of the old one goes (§11.6 case 7).
        if (staleDraftRef.current) {
          void deleteEmail(staleDraftRef.current);
          staleDraftRef.current = null;
        }
        setDraftStatus("saved");
      } catch {
        setDraftStatus("idle");
      }
    }, 1200);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [to, cc, subject, body, open, fromId]);

  // Which mailbox last wrote to each recipient, for the From warning. It
  // runs only for a member with two or more mailboxes, after a short pause.
  useEffect(() => {
    if (!open || accounts.length < 2) return;
    const list = [...to.split(","), ...cc.split(",")].map((s) => s.trim()).filter(Boolean);
    let alive = true;
    const handle = setTimeout(() => {
      void getSentFrom(list).then((map) => {
        if (alive) setUsualSender(map);
      });
    }, 600);
    return () => {
      alive = false;
      clearTimeout(handle);
    };
  }, [to, cc, open, accounts.length]);

  if (!open) return null;

  const fromAccount = accounts.find((a) => a.id === fromId) ?? null;
  const fromBlocked = needsReconnect(fromAccount, authErrors);
  const warning = fromWarning({
    fromId,
    accounts,
    conversationAccountId: conversationId,
    recipients: [...to.split(","), ...cc.split(",")],
    usualSender,
    authErrors,
  });

  /** Change the sending mailbox. The signature follows it, and a saved
   *  draft moves to the new mailbox on the next save. */
  const changeFrom = async (next: string) => {
    if (!next || next === fromId) return;
    const [oldSig, newSig] = await Promise.all([
      getSignatureText(fromId), getSignatureText(next),
    ]);
    if (draftIdRef.current) {
      staleDraftRef.current = draftIdRef.current;
      draftIdRef.current = null;
    }
    dirty.current = true;
    setBody((prev) => swapSignature(prev, oldSig, newSig));
    setFromPick({ base: accountId, id: next });
  };

  const handleSend = async () => {
    if (!to.trim() || sending) return;
    if (fromBlocked) {
      setSendError(`Reconnect ${fromAccount ? mailboxLabel(fromAccount) : "this mailbox"} to send from it.`);
      return;
    }
    setSending(true);
    setSendError(null);
    const toArr = to.split(",").map((s) => s.trim()).filter(Boolean);
    const ccArr = cc ? cc.split(",").map((s) => s.trim()).filter(Boolean) : [];
    const hasAttachments = attachments.length > 0 || artifacts.length > 0;
    try {
      // Native draft-send whenever there's a draft OR attachments: Cc/Bcc AND
      // attachment content now ride ON the provider draft, so the draft write-
      // path (create/update with attachments) → native send handles everything.
      // A fresh message with no attachments still sends directly.
      if (draftIdRef.current || hasAttachments) {
        const saved = await saveDraft({
          accountId: fromId,
          draftId: draftIdRef.current ?? undefined,
          replyToMessageId: draftIdRef.current
            ? undefined : (replyTarget || undefined),
          to: toArr,
          cc: ccArr,
          subject,
          body: combinedBody(),
          attachments: attachments.length ? attachments : undefined,
          artifacts: artifacts.length ? artifacts : undefined,
        });
        await sendDraft(fromId, saved.id);
        if (staleDraftRef.current) void deleteEmail(staleDraftRef.current);
        onClose();
      } else {
        await onSend({
          accountId: fromId,
          to: toArr,
          cc: ccArr.length ? ccArr : undefined,
          subject,
          bodyText: combinedBody(),
          replyToMessageId: replyTarget,
          attachments: attachments.length ? attachments : undefined,
          artifacts: artifacts.length ? artifacts : undefined,
        });
        if (draftIdRef.current) void deleteEmail(draftIdRef.current);
        if (staleDraftRef.current) void deleteEmail(staleDraftRef.current);
        // onClose is called by the store after successful send
      }
    } catch (err: any) {
      setSendError(err.message || "Failed to send");
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-12 sm:pt-20 px-4">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />

      {/* Compose window — height-capped (viewport minus the pt-12 offset and
          the safe-area inset) with the fields scrolling inside, so the footer
          with Send/Discard is always on screen, even on short phone viewports. */}
      <div className="relative w-full max-w-2xl bg-card border border-border rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[calc(100dvh-5rem-env(safe-area-inset-bottom,0px))] sm:max-h-[85vh]">
        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-4 py-3 border-b border-border bg-secondary/50">
          <span className="text-sm font-medium text-foreground">New Message</span>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" onClick={onClose} className="rounded">
            <Icon name="X" size={16} />
          </Button>
        </div>

        {/* Fields — the scrolling region when the window hits its max height */}
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-3">
          {/* From — two or more mailboxes (EM-T8c, D-EM-20) */}
          <FromRow
            accounts={accounts}
            value={fromId}
            onChange={(id) => void changeFrom(id)}
            warning={warning}
            authErrors={authErrors}
          />

          {/* To */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground w-8 flex-shrink-0">To:</span>
            <RecipientInput
              value={to}
              onChange={(v) => { dirty.current = true; setTo(v); }}
              accountId={fromId}
              ariaLabel="To recipients"
              placeholder="Email address..."
              className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none"
            />
          </div>

          {/* Cc */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground w-8 flex-shrink-0">Cc:</span>
            <RecipientInput
              value={cc}
              onChange={(v) => { dirty.current = true; setCc(v); }}
              accountId={fromId}
              ariaLabel="Cc recipients"
              placeholder="Cc..."
              className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none"
            />
          </div>

          {/* Subject */}
          <div className="flex items-center gap-2 border-t border-border pt-3">
            <span className="text-xs text-muted-foreground w-8 flex-shrink-0">Subj:</span>
            <input
              type="text"
              value={subject}
              onChange={(e) => { dirty.current = true; setSubject(e.target.value); }}
              placeholder="Subject..."
              className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none"
            />
          </div>

          {/* Body */}
          <div className="border-t border-border pt-3">
            <textarea
              value={body}
              onChange={(e) => { dirty.current = true; setBody(e.target.value); }}
              placeholder="Write your message..."
              rows={12}
              autoFocus
              className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none resize-none leading-relaxed"
            />
          </div>

          {/* Quoted trailing email — collapsed, read-only (reattached on send) */}
          <ComposerQuote quote={quote || ""} className="pb-1" />

          {/* Attachment chips */}
          {(attachments.length > 0 || artifacts.length > 0) && (
            <div className="border-t border-border pt-2 flex flex-wrap gap-1.5">
              {attachments.map((a, i) => (
                <span
                  key={`f-${i}`}
                  className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border border-border bg-secondary text-muted-foreground"
                >
                  <Icon name="Paperclip" size={10} />
                  <span className="truncate max-w-[160px]" title={a.filename}>{a.filename}</span>
                  <button
                    onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                    className="hover:text-foreground"
                    title="Remove attachment"
                  >
                    <Icon name="X" size={10} />
                  </button>
                </span>
              ))}
              {artifacts.map((a, i) => (
                <span
                  key={`a-${i}`}
                  className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border border-primary/40 bg-primary/5 text-primary"
                  title={a.path}
                >
                  <Icon name="Paperclip" size={10} />
                  <span className="truncate max-w-[160px]">{a.name || a.path}</span>
                  <button
                    onClick={() => setArtifacts((prev) => prev.filter((_, j) => j !== i))}
                    className="hover:text-foreground"
                    title="Remove attachment"
                  >
                    <Icon name="X" size={10} />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* AI draft/improve bar */}
        {aiOpen && (
          <DraftAssistant
            instruction={aiInstruction}
            onInstruction={setAiInstruction}
            busy={ai.busy}
            hasText={stripSignature(body, sigText).trim().length > 0}
            hasDraft={ai.hasDraft}
            steps={ai.steps}
            thinking={ai.thinking}
            revisions={ai.revisions}
            activeRevision={ai.activeRevision}
            elapsedMs={ai.elapsedMs}
            onRun={runAi}
            onRestore={(id) => ai.restore(id, splitQuotedText(body).quoted)}
            onClose={() => setAiOpen(false)}
          />
        )}

        {/* Footer */}
        <div className="shrink-0 px-4 py-3 border-t border-border flex items-center justify-between">
          <div className="flex-1">
            {sendError ? (
              <span className="text-[10px] text-red-500">{sendError}</span>
            ) : draftStatus === "saving" ? (
              <span className="text-[10px] text-muted-foreground">Saving draft…</span>
            ) : draftStatus === "saved" ? (
              <span className="text-[10px] text-muted-foreground">Draft saved to Drafts</span>
            ) : (
              <span className="text-[10px] text-muted-foreground">
                {fromAccount
                  ? `From ${mailboxLabel(fromAccount)} · ${fromAccount.emailAddress}`
                  : "Sent from your connected email account"}
              </span>
            )}
          </div>
          <div className="flex gap-2 items-center">
            <AiButton active={aiOpen} onClick={() => setAiOpen((v) => !v)} />
            <label
              className="px-2 py-1.5 text-xs rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors cursor-pointer flex items-center"
              title="Attach files"
            >
              <Icon name="Paperclip" size={14} />
              <input
                type="file"
                multiple
                className="hidden"
                onChange={(e) => { void addFiles(e.target.files); e.target.value = ""; }}
              />
            </label>
            <ArtifactAttachPicker
              exclude={artifacts.map((a) => a.path)}
              onPick={(ref) => setArtifacts((prev) =>
                prev.some((a) => a.path === ref.path) ? prev : [...prev, ref])}
            />
            <Button variant="ghost" size="none" radius="keep" layout="" onClick={() => {
                // Discard removes the auto-saved draft (closing via X keeps it).
                if (draftIdRef.current) void deleteEmail(draftIdRef.current);
                onClose();
              }} disabled={sending} className="px-3 py-1.5 text-xs rounded-md">
              Discard
            </Button>
            <Button size="none" radius="keep" layout="flex items-center" onClick={handleSend} disabled={sending || !to.trim()} className="px-4 py-1.5 text-xs rounded-md gap-1.5">
              {sending && <Icon name="Loader2" size={12} className="animate-spin" />}
              {sending ? "Sending…" : "Send"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
