"use client";

import Button from "@/components/ui/Button";
import AppIcon from "@/components/Icon";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Email } from "../lib/types";
import { fullDateLabel, initials, buildOptimisticSent } from "../lib/utils";
import { getEmail, fetchFullBody, detectReplyCommitment } from "../lib/api";
import { useDraftSession } from "../lib/useDraftSession";
import { splitQuotedText } from "../lib/quoting";
import { useEmailStore, withPatch } from "../lib/emailStore";
import { ComposerQuote, AiButton } from "./ComposerAI";
import { DraftAssistant } from "./DraftAssistant";
import { ComposerBody, ComposerExpandButton, useComposerSize } from "./ComposerSize";
import { useViewMode } from "@/components/ViewModeProvider";
import { MessageContent } from "./MessageContent";
import { remoteHtmlId } from "../lib/htmlPrefetch";
import { AttachmentList } from "./AttachmentList";
import { getSignatureText, stripSignature } from "../lib/signature";
import { RecipientInput } from "./RecipientInput";
import { draftRecipients, ownAddresses, replyRecipients } from "../lib/mailbox";
import { TaskCaptureModal, type CommitmentContext } from "./TaskCaptureModal";
import { ContactTrigger, RecipientList } from "./ContactCard";
import {
  autosaveWait, createAutosave, failedSaveStatus, saveFailureText, sendFailureText,
  type DraftStatus,
} from "../lib/draftAutosave";
import {
  draftSavePayload, draftText, isHtmlUnavailable, needsHtmlRead, noticeAfterSave,
  noticeWords, sendAfterPreSave, showsFormattingNotice,
} from "../lib/draftSave";

const isDraft = (m: Email) =>
  (m.folder || "").toLowerCase() === "drafts" ||
  (m.folder || "").toLowerCase() === "draft";

const isTrashed = (m: Email) => {
  const f = (m.folder || "").toLowerCase();
  return f === "trash" || f === "deleted";
};

const INPUT =
  "w-full bg-background border border-border rounded-md px-2.5 py-2 text-xs " +
  "text-foreground outline-none focus:border-primary transition-colors";

/**
 * Gmail-style conversation view: the messages of a thread stacked oldest→newest.
 * Collapsed cards show sender + snippet; the opened message and the latest are
 * expanded by default. Bodies hydrate lazily when a card is expanded. Draft
 * messages in the thread render as an editable composer you can send or discard.
 */
export function ConversationView({
  messages,
  openedId,
  renderActions,
  lightVersions,
  onSent,
}: {
  messages: Email[];
  openedId: string;
  /** The action row of one card (`MessageActions`): Reply, Reply all,
   *  Forward and "More actions". The reading pane owns every handler, so
   *  each card acts on its own message. */
  renderActions?: (message: Email) => ReactNode;
  /** The messages whose light version the member asked for. */
  lightVersions?: ReadonlySet<string>;
  /** Called after an in-thread draft is sent so the parent can surface the
   *  reply immediately and pull the real synced copy. */
  onSent?: (sent?: Email) => void;
}) {
  // The last change of this pane to each message (read, flag, star, labels).
  const messagePatches = useEmailStore((s) => s.messagePatches);
  // Locally-discarded drafts hide instantly (the provider delete is async).
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  // Hide messages deleted upstream (trash) so the conversation reflects the
  // live mailbox — except the one the user explicitly opened — and any draft the
  // user just discarded.
  const visible = messages.filter(
    (m) => (!isTrashed(m) || m.id === openedId) && !dismissed.has(m.id)
  );
  const lastId = visible[visible.length - 1]?.id;
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set([openedId, lastId].filter(Boolean) as string[])
  );
  const [hydrated, setHydrated] = useState<Record<string, Email>>({});
  // A detected commitment awaiting the user's confirm in the "Add to Tasks"
  // popup, plus the account it belongs to (null = no popup open).
  const [commitment, setCommitment] = useState<
    { accountId: string; ctx: CommitmentContext } | null
  >(null);

  // The newest non-draft message — the one a draft reply is threaded onto.
  const replyTarget = [...visible].reverse().find((m) => !isDraft(m));

  // After a reply sends, ask the backend whether it committed the user to a
  // task. Only when it did do we surface the popup — a silent no-op otherwise,
  // so a plain "thanks, will do" never interrupts. Best-effort: a detector
  // failure just means no popup, never a broken send.
  const checkCommitment = async (c: {
    accountId: string;
    threadId: string;
    subject: string;
    body: string;
    replyToMessageId: string | null;
  }) => {
    try {
      const res = await detectReplyCommitment({
        accountId: c.accountId,
        threadId: c.threadId,
        body: c.body,
        subject: c.subject,
        replyToMessageId: c.replyToMessageId,
      });
      if (!res.isCommitment || !res.draft) return;
      setCommitment({
        accountId: c.accountId,
        ctx: {
          threadId: c.threadId,
          body: c.body,
          subject: c.subject,
          replyToMessageId: c.replyToMessageId,
          draft: res.draft,
          similar: res.similar,
        },
      });
    } catch {
      /* detection is best-effort — no popup on failure */
    }
  };

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  // Hydrate expanded messages that arrived incomplete: either body-less, or
  // carrying attachments (has_attachments) whose file rows weren't included in
  // the thread payload — so each card can show its own attachment thumbnails.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    visible.forEach((m) => {
      const needsBody = !m.bodyHtml && !m.bodyText;
      const needsAttachments =
        m.hasAttachments && (!m.attachments || m.attachments.length === 0);
      if (
        expanded.has(m.id) &&
        !hydrated[m.id] &&
        (needsBody || needsAttachments) &&
        !isDraft(m)
      ) {
        getEmail(m.id)
          .then((full) => setHydrated((h) => ({ ...h, [m.id]: full })))
          // On failure, cache the list row so the effect doesn't retry this
          // message forever every time `hydrated` changes for another card.
          .catch(() => setHydrated((h) => ({ ...h, [m.id]: m })));
      }
    });
  }, [expanded, visible, hydrated]);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <div className="flex flex-col gap-2">
      {visible.map((m) => {
        if (isDraft(m)) {
          return (
            <DraftCard
              key={m.id}
              draft={m}
              replyTo={replyTarget}
              onSent={onSent}
              onCommitment={checkCommitment}
              onDismiss={() =>
                setDismissed((s) => new Set(s).add(m.id))
              }
            />
          );
        }
        const isOpen = expanded.has(m.id);
        // The hydrated copy holds the body and the files. Its read, flag,
        // star, folder and labels come from the thread row, which the pane
        // keeps current, and a write in flight shows on top (fix rounds 1
        // and 3).
        const full = hydrated[m.id];
        const base = full
          ? { ...full, isRead: m.isRead, isStarred: m.isStarred, isFlagged: m.isFlagged, folder: m.folder, categories: m.categories }
          : m;
        const view = withPatch(base, messagePatches);
        return (
          <div
            key={m.id}
            className={`border border-border rounded-lg overflow-hidden ${
              isOpen ? "" : "bg-secondary/20"
            }`}
          >
            {/* The row toggles the message; the avatar and name inside it open
                the sender's contact card instead. A div (not a button) so those
                nested triggers are real buttons rather than fake ones. */}
            <div
              role="button"
              tabIndex={0}
              aria-expanded={isOpen}
              onClick={() => toggle(m.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle(m.id);
                }
              }}
              className="w-full flex items-center gap-2 px-3 py-2 text-left cursor-pointer hover:bg-secondary/40 transition-colors"
            >
              <ContactTrigger
                contact={m.from}
                accountId={m.accountId}
                className="w-7 h-7 rounded-full bg-primary/20 text-primary flex items-center justify-center text-[10px] font-semibold flex-shrink-0 hover:ring-2 hover:ring-primary/40 transition-shadow"
              >
                {initials(m.from.name)}
              </ContactTrigger>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-foreground truncate flex items-center gap-1">
                  <ContactTrigger
                    contact={m.from}
                    accountId={m.accountId}
                    className="truncate hover:text-primary hover:underline transition-colors"
                  >
                    {m.from.name}
                  </ContactTrigger>
                  {!m.isRead && (
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-primary" />
                  )}
                </div>
                {!isOpen && (
                  <div className="text-[11px] text-muted-foreground truncate">
                    {m.snippet}
                  </div>
                )}
              </div>
              {m.hasAttachments && (
                <AppIcon name="Paperclip" size={11} className="text-muted-foreground flex-shrink-0" />
              )}
              <span className="text-[10px] text-muted-foreground whitespace-nowrap flex-shrink-0">
                {fullDateLabel(m.receivedAt)}
              </span>
              <AppIcon name="ChevronDown"
                size={13}
                className={`text-muted-foreground flex-shrink-0 transition-transform ${
                  isOpen ? "rotate-180" : ""
                }`}
              />
            </div>
            {isOpen && (
              <div className="px-3 pb-3">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="min-w-0 truncate text-[11px]">
                    <RecipientList
                      label="To"
                      people={m.to}
                      accountId={m.accountId}
                    />
                  </div>
                  {/* A direct child: the row measures this line for its width. */}
                  {renderActions?.(view)}
                </div>
                {view.bodyHtml || view.bodyText ? (
                  <MessageContent
                    html={view.bodyHtml}
                    text={view.bodyText}
                    remoteId={remoteHtmlId(view)}
                    lightVersion={lightVersions?.has(view.id) ?? false}
                    messageId={view.id}
                  />
                ) : (
                  <div className="text-xs text-muted-foreground italic py-2">
                    No preview text.
                  </div>
                )}
                {/* Per-message attachments — same card UI (with image
                    thumbnails) as the single-message reader, so a thread's
                    earlier messages surface their files too. */}
                {view.hasAttachments && (
                  <AttachmentList
                    attachments={view.attachments}
                    className="mt-4 pt-3 border-t border-border"
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
      {/* "You committed to a task" popup — same UI as an inbound capture, keyed
          on the just-sent reply. Opens only when the detector found a real
          commitment; dismissible (Cancel / Esc / backdrop). */}
      {commitment && (
        <TaskCaptureModal
          accountId={commitment.accountId}
          commitment={commitment.ctx}
          onClose={() => setCommitment(null)}
          onCaptured={() => setCommitment(null)}
        />
      )}
    </div>
  );
}

export const isDraftEmail = isDraft;

/** Inline editable draft: edit the body/recipients and send it into the thread. */
export function DraftCard({
  draft,
  replyTo,
  onDismiss,
  onSent,
  onCommitment,
}: {
  draft: Email;
  replyTo?: Email;
  onDismiss?: () => void;
  onSent?: (sent?: Email) => void;
  /** After a reply is sent, hand its context up so the parent can check whether
   *  it committed the user to a task (and offer the "Add to Tasks" popup). Only
   *  fired for real replies (a reply target exists), not from-scratch composes. */
  onCommitment?: (ctx: {
    accountId: string;
    threadId: string;
    subject: string;
    body: string;
    replyToMessageId: string | null;
  }) => void;
}) {
  const {
    deleteEmail, selectedAccountId, saveDraft, sendDraft, accounts,
  } = useEmailStore();
  // The mailbox of the draft sends. Reply-all leaves out EVERY address of the
  // member, not only this one (D-EM-27, MB-7, EM-T8a review).
  const own = ownAddresses(accounts);
  const sendingAddress = accounts
    .find((a) => a.id === (draft.accountId || selectedAccountId))
    ?.emailAddress;
  // The provider of the mailbox. Only a Gmail draft keeps its HTML, reads it,
  // or shows the notice (EM-G3a-f7, D-G3a-f7-5).
  const provider = accounts
    .find((a) => a.id === (draft.accountId || selectedAccountId))
    ?.provider;
  const gmail = provider === "gmail";
  // A real inbound message to reply to (vs a from-scratch / compose draft) —
  // gates the Reply / Reply All toggle and the reply-all recipient maths.
  const hasReplyTarget = !!replyTo && replyTo.id !== draft.id;
  const draftTo = draft.to.map((t) => t.email).filter(Boolean);
  // REPLY-ALL recipients: the original sender + everyone on To, minus the
  // member; original Cc carried over. Falls back to what the draft has.
  const all = hasReplyTarget && replyTo
    ? replyRecipients(replyTo, "reply-all", own, sendingAddress)
    : null;
  const replyAllTo = all && all.to.length ? all.to : draftTo;
  const replyAllCc = all ? all.cc : [];
  // REPLY (sender only) recipients.
  const only = hasReplyTarget && replyTo
    ? replyRecipients(replyTo, "reply", own, sendingAddress)
    : null;
  const replyOnlyTo = only && only.to.length ? only.to : draftTo;

  // Split any quoted trailing chain out of the draft body so the editable box
  // holds only the new text (and AI never rewrites the quote); it's reattached
  // on send. `initSplit` covers a draft that already arrived with a body.
  const initSplit = splitQuotedText(draft.bodyText || "");
  const [hydratedBody, setHydratedBody] = useState<string | null>(null);
  const [body, setBody] = useState(initSplit.main);
  const [quote, setQuote] = useState(initSplit.quoted || "");
  // The text of the card at open (EM-G3a-f7, D-G3a-f7-2). A save with this
  // text keeps the HTML of a Gmail draft. For a draft that came with no body,
  // the text at open is the text that the hydrate put in the card.
  const [initialText] = useState(() => draftText(initSplit.main, initSplit.quoted || ""));
  const openedText = hydratedBody === null
    ? initialText
    : (() => {
        const sp = splitQuotedText(hydratedBody);
        return draftText(sp.main, sp.quoted || "");
      })();
  // The card starts with the To, Cc and Bcc of its draft, and with the button
  // that matches them, or neither (EM-T10). Before, it started on Reply All
  // and wrote those lists over a reply that the member had narrowed.
  const [start] = useState(() =>
    draftRecipients(
      draft,
      all ? { to: replyAllTo, cc: replyAllCc } : null,
      only ? replyOnlyTo : null,
    ),
  );
  const [to, setTo] = useState(start.to.join(", "));
  const [cc, setCc] = useState(start.cc.join(", "));
  const [bcc, setBcc] = useState(start.bcc.join(", "));
  // `null` marks neither button: the lists match no click of the toggle.
  const [replyAll, setReplyAll] = useState<boolean | null>(start.replyAll);
  // The Cc row shows for a Cc or a Bcc, and on a reply that is not Reply.
  const [showCc, setShowCc] = useState(start.showCc);
  // An edit of the member. Only then does the autosave run. It is declared
  // before its first reader, so the React lint knows it is a ref.
  const dirty = useRef(false);

  /** Flip Reply ↔ Reply All, recomputing To/Cc from the original message.
   *  Reply All reveals the Cc/Bcc fields; Reply (sender only) hides them and
   *  clears their contents so you don't silently keep other recipients. */
  const applyReplyAll = (next: boolean) => {
    setReplyAll(next);
    dirty.current = true;
    if (next) {
      setTo(replyAllTo.join(", "));
      setCc(replyAllCc.join(", "));
      setShowCc(true);
    } else {
      setTo(replyOnlyTo.join(", "));
      setCc("");
      setBcc("");
      setShowCc(false);
    }
  };
  const [sending, setSending] = useState(false);
  // The text of a failed send. Before EM-G3c-2 the card dropped it (item 14).
  const [sendError, setSendError] = useState<string | null>(null);
  // EM-G3a-f7: the HTML that the card read for a Gmail draft with none, a
  // save that answered `lost`, and a stopped send. The member saw the notice
  // after a stop, so the next press sends the text only (D-G3a-f7-11, -12).
  const [htmlRead, setHtmlRead] = useState<{ html: string | null; failed: boolean } | null>(null);
  const htmlReadStarted = useRef(false);
  const [htmlLost, setHtmlLost] = useState(false);
  // The send reads the loss through a ref. A press during an autosave that
  // answers `lost` then still sees it after the drain (review round 1, P2).
  const htmlLostRef = useRef(false);
  const markLost = () => {
    htmlLostRef.current = true;
    setHtmlLost(true);
  };
  const [textOnlyConfirmed, setTextOnlyConfirmed] = useState(false);
  // A stopped send shows its own words, until the next send or a text edit.
  const [sendStopped, setSendStopped] = useState(false);
  /** Stop the send: the next press sends the text only, and the card says so. */
  const stopSend = () => {
    setTextOnlyConfirmed(true);
    setSendStopped(true);
  };
  const [draftStatus, setDraftStatus] = useState<DraftStatus>("idle");
  // The pending autosave. An unmount and a change of draft run it at once
  // (EM-G3c-2 item 11).
  const [autosave] = useState(() => createAutosave());
  // AI draft/refine panel — the session owns live backend steps + revisions.
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInstruction, setAiInstruction] = useState("");
  // The signature's plain text — it rides IN the stored draft body now (the
  // draft-write paths sign it), so the editor shows it as ordinary body text;
  // kept here to judge whether the user wrote anything beyond it.
  const [sigText, setSigText] = useState("");
  useEffect(() => {
    let alive = true;
    void getSignatureText(draft.accountId || selectedAccountId).then((s) => {
      if (alive) setSigText(s);
    });
    return () => {
      alive = false;
    };
  }, [draft.accountId, selectedAccountId]);
  const ai = useDraftSession({
    onBody: (next) => {
      dirty.current = true;
      setSendStopped(false);
      setBody(next);
    },
  });
  // The size of the body: auto-grow, a dragged height, or the whole pane
  // (owner request, 2026-10-14). The inline reply shares the same hook.
  const cardRef = useRef<HTMLDivElement>(null);
  const { isMobile } = useViewMode();
  const size = useComposerSize(cardRef, true, isMobile);
  const expanded = size.state.expanded;

  /** The full outgoing body: the editable text plus the quoted trailing chain. */
  const combinedBody = () =>
    draftText(body, quote);

  const ccList = () => cc.split(",").map((s) => s.trim()).filter(Boolean);
  const bccList = () => bcc.split(",").map((s) => s.trim()).filter(Boolean);

  // The draft almost always arrives body-less: provider drafts (incl. the ones
  // the AI agent creates) sync header-only, so the DB copy has no body. Pull the
  // DB row first, then fall back to the provider's authoritative draft body so
  // the editor is pre-filled with the actual AI draft — not an empty box.
  useEffect(() => {
    if ((draft.bodyText || draft.bodyHtml) || hydratedBody !== null) return;
    let cancelled = false;
    (async () => {
      let text = "";
      try {
        const full = await getEmail(draft.id);
        text = full.bodyText || "";
      } catch {
        /* fall through to provider fetch */
      }
      if (!text) {
        try {
          const fb = await fetchFullBody(draft.id);
          text = fb.body_text || "";
        } catch {
          /* leave empty */
        }
      }
      if (cancelled) return;
      setHydratedBody(text);
      const sp = splitQuotedText(text);
      setBody((b) => (b ? b : sp.main));
      setQuote((q) => q || sp.quoted || "");
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.id]);

  // EM-G3a-f7 (B5): a Gmail draft that came with no HTML reads it once, so
  // the card knows to show the notice. The hydrate above returns early for a
  // draft with text, so this read is an effect of its own.
  useEffect(() => {
    if (!needsHtmlRead(provider, draft.bodyHtml, htmlReadStarted.current)) return;
    htmlReadStarted.current = true;
    fetchFullBody(draft.id)
      .then((fb) => setHtmlRead({ html: fb.body_html, failed: false }))
      .catch(() => setHtmlRead({ html: null, failed: true }));
  }, [provider, draft.id, draft.bodyHtml]);

  const recipients = () =>
    to.split(",").map((s) => s.trim()).filter(Boolean);

  // Auto-save edits to the draft in place (debounced) so changes persist to the
  // provider Drafts and survive a refresh — keyed on the draft's own id.
  useEffect(() => {
    const accountId = draft.accountId || selectedAccountId;
    if (!accountId || !dirty.current) {
      autosave.cancel();
      return;
    }
    autosave.schedule(async () => {
      try {
        setDraftStatus("saving");
        const saved = await saveDraft(draftSavePayload({
          accountId,
          draftId: draft.id,
          to: recipients(),
          cc: ccList(),
          bcc: bccList(),
          subject: draft.subject || "",
        }, openedText, combinedBody(), provider));
        if (noticeAfterSave(saved)) markLost();
        setDraftStatus("saved");
      } catch (err) {
        // The next edit tries again (EM-G3c-2 item 13).
        setDraftStatus(failedSaveStatus(err));
      }
    }, autosaveWait(
      accounts.find((a) => a.id === accountId)?.provider,
      draft.hasAttachments,
    ), 0);
    // Stop the timer and keep the save, so an unmount can still flush it.
    return () => autosave.hold();
    // An edit of the Cc or the Bcc saves too (EM-T10 item 4, EM-G3c-2-f2).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [body, quote, to, cc, bcc]);

  // An unmount, and a switch of the card to another draft, run the pending
  // save at once (EM-G3c-2 item 11). The save names the draft of before.
  useEffect(() => () => autosave.flush(), [autosave, draft.id]);

  const send = async () => {
    const accountId = draft.accountId || selectedAccountId;
    if (!accountId || recipients().length === 0) return;
    setSending(true);
    setSendError(null);
    setSendStopped(false);
    // The send carries the last edit, so each queued autosave goes. A save
    // that runs settles first, so no older text lands after this save
    // (EM-G3c-2 review round 2). The key gives each draft its own card, so
    // the card has one session, 0 (EM-G3c-3 item 1).
    await autosave.drain(0);
    try {
      // Persist the latest edits — Cc/Bcc included, now carried on the provider
      // draft — then send THIS draft natively (Drafts → Sent, no duplicate).
      // sendDraft removes it from the list. No more full-send detour for a Cc'd
      // reply, which used to start a fresh (unthreaded) message.
      // A save with the text at open keeps the HTML of a Gmail draft
      // (EM-G3a-f7, D-G3a-f7-2).
      const saved = await saveDraft(draftSavePayload({
        accountId,
        draftId: draft.id,
        to: recipients(),
        cc: ccList(),
        bcc: bccList(),
        subject: draft.subject || "",
      }, openedText, combinedBody(), provider));
      if (noticeAfterSave(saved)) markLost();
      // A `lost` stops the first press, and the card shows the notice. The
      // next press sends the text only (D-G3a-f7-12). A `lost` of an earlier
      // autosave stops it too, because this save then answers `none`.
      if (sendAfterPreSave(saved, textOnlyConfirmed, htmlLostRef.current) === "stop") {
        stopSend();
        return;
      }
      // A Gmail send asks for a 409 when the gateway cannot read the HTML,
      // until the member saw the notice. Each other send is as on main.
      await sendDraft(accountId, draft.id, gmail ? textOnlyConfirmed : undefined);
      // Surface the reply in the conversation at once + pull the real copy.
      const sentThreadId = replyTo?.threadId || draft.threadId;
      onSent?.(
        buildOptimisticSent({
          accountId,
          threadId: sentThreadId,
          fromEmail: sendingAddress?.toLowerCase() || "",
          to: recipients(),
          cc: ccList(),
          subject: draft.subject || "",
          bodyText: combinedBody(),
        })
      );
      // If this was a real reply, check whether it committed me to a task. Pass
      // only the NEW reply text (`body`), not the quoted chain, so the detector
      // reasons about what I just wrote — not the whole thread as my words.
      if (hasReplyTarget && sentThreadId && body.trim()) {
        onCommitment?.({
          accountId,
          threadId: sentThreadId,
          subject: draft.subject || "",
          body,
          replyToMessageId: replyTo?.providerMessageId || null,
        });
      }
    } catch (err) {
      // The draft stays in Drafts so the user can retry. The card shows why,
      // and a 413 shows "This mail is too large to send." (EM-G3c-2 item 14).
      // The 409 of a Gmail draft whose HTML the gateway cannot read shows the
      // notice, and the next press sends the text only (D-G3a-f7-11).
      if (isHtmlUnavailable(err)) stopSend();
      else setSendError(sendFailureText(err));
    } finally {
      setSending(false);
    }
  };

  const discard = async () => {
    if (!confirm("Discard this draft?")) return;
    // The chain drains first, so no save that waited writes the draft again.
    // The delete waits for the save that runs (EM-G3c-2 review round 2).
    const drained = autosave.drain(0);
    onDismiss?.(); // hide instantly; the provider delete is async
    await drained;
    try {
      await deleteEmail(draft.id);
    } catch {
      /* already hidden locally; the sweep will reconcile */
    }
  };

  /** Draft or refine the reply with AI — operates on the NEW text only (the
   *  quoted trailing chain is kept separate, never sent). Backend steps stream
   *  into the panel, and it stays open so the next instruction refines this
   *  draft rather than starting from scratch. */
  const runAi = async () => {
    const accountId = draft.accountId || selectedAccountId;
    if (!accountId || ai.busy) return;
    const instruction = aiInstruction.trim();
    const result = await ai.run(
      {
        accountId,
        body,
        instruction,
        mode: replyTo ? "reply" : "new",
        messageId: replyTo?.id,
        to: recipients(),
        subject: draft.subject || "",
      },
      { instruction },
    );
    if (result) setAiInstruction("");
  };

  // "Not saved" or "Too large to save" after a failed save (EM-G3c-2 item 13).
  const saveFailure = saveFailureText(draftStatus);
  // The notice of a Gmail draft with HTML (EM-G3a-f7, D-G3a-f7-3). It also
  // shows after a save that lost the HTML, and after a stopped send. It shows
  // while the HTML read runs, so the member knows before the first edit.
  const showNotice =
    showsFormattingNotice(
      provider,
      draft.bodyHtml || htmlRead?.html,
      htmlRead?.failed ?? false,
      needsHtmlRead(provider, draft.bodyHtml, htmlRead !== null),
    ) || htmlLost || textOnlyConfirmed;

  return (
    <div
      ref={cardRef}
      data-email-composer=""
      style={size.rootStyle}
      className={`border border-primary/40 rounded-lg bg-primary/5 px-3 py-3 ${size.rootClass}`}
    >
      <div className="flex items-center gap-1.5 mb-2">
        <AppIcon name="PenLine" size={12} className="text-primary" />
        <span className="text-xs font-medium text-primary">Draft</span>
        {hasReplyTarget && (
          <div className="flex items-center bg-background border border-border rounded-md p-0.5 ml-1.5">
            <button
              type="button"
              onClick={() => applyReplyAll(false)}
              title="Reply to sender only"
              className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] whitespace-nowrap transition-colors ${
                replyAll === false ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <AppIcon name="Reply" size={11} className="flex-shrink-0" /> Reply
            </button>
            <button
              type="button"
              onClick={() => applyReplyAll(true)}
              title="Reply to everyone"
              className={`flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] whitespace-nowrap transition-colors ${
                replyAll === true ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <AppIcon name="ReplyAll" size={11} className="flex-shrink-0" /> Reply All
            </button>
          </div>
        )}
        <span className="text-[10px] text-muted-foreground ml-auto truncate max-w-[45%]">
          {draft.subject}
        </span>
        <ComposerExpandButton
          expanded={expanded}
          onToggle={() => size.dispatch({ type: "toggleExpand" })}
        />
      </div>
      <div className={`space-y-2 ${expanded ? "flex flex-1 flex-col" : ""}`}>
        <div className="flex items-center gap-2">
          <RecipientInput
            value={to}
            onChange={(v) => { dirty.current = true; setTo(v); }}
            accountId={draft.accountId || selectedAccountId}
            ariaLabel="To recipients"
            placeholder="To (comma-separated)"
            className={`${INPUT} w-full`}
          />
          {!showCc && (
            <Button variant="text" size="none" layout="" type="button" onClick={() => setShowCc(true)} className="text-[10px] whitespace-nowrap px-1">
              Cc/Bcc
            </Button>
          )}
        </div>
        {showCc && (
          <>
            <RecipientInput
              value={cc}
              onChange={(v) => { dirty.current = true; setCc(v); }}
              accountId={draft.accountId || selectedAccountId}
              ariaLabel="Cc recipients"
              placeholder="Cc (comma-separated)"
              wrapperClassName="relative"
              className={`${INPUT} w-full`}
            />
            <RecipientInput
              value={bcc}
              onChange={(v) => { dirty.current = true; setBcc(v); }}
              accountId={draft.accountId || selectedAccountId}
              ariaLabel="Bcc recipients"
              placeholder="Bcc (comma-separated)"
              wrapperClassName="relative"
              className={`${INPUT} w-full`}
            />
          </>
        )}
        {showNotice && (
          <p
            role={sendStopped ? "alert" : "status"}
            className="flex items-start gap-1.5 rounded-md border border-warning/30 bg-warning/10 px-2.5 py-2 text-[11px] leading-snug text-warning"
          >
            <AppIcon name="AlertTriangle" size={12} className="mt-px flex-shrink-0" />
            <span>{noticeWords(sendStopped)}</span>
          </p>
        )}
        <ComposerBody size={size} value={body}>
        <textarea
          value={body}
          onChange={(e) => { dirty.current = true; setSendStopped(false); setBody(e.target.value); }}
          rows={7}
          placeholder="Write your reply…"
          className={`${INPUT} resize-none leading-relaxed`}
        />
        </ComposerBody>
        {/* The signature is IN the stored draft body (signed at draft-write
            time) — no separate preview card. */}
        {/* Quoted trailing email — collapsed, read-only (reattached on send) */}
        <ComposerQuote quote={quote} className="" />
      </div>
      {aiOpen && (
        <div className="mt-2 -mx-3 border-y border-border">
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
            notice={ai.notice}
            onStop={ai.stop}
            onRun={runAi}
            onRestore={(id) => ai.restore(id)}
            onClose={() => setAiOpen(false)}
          />
        </div>
      )}
      <div className="flex items-center gap-2 mt-2">
        <Button layout="flex items-center" onClick={send} disabled={sending || recipients().length === 0}>
          {sending ? (
            <AppIcon name="Loader2" className="animate-spin" size={13} />
          ) : (
            <AppIcon name="Send" size={13} />
          )}
          Send
        </Button>
        <button
          onClick={discard}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border text-xs text-muted-foreground hover:text-destructive hover:bg-secondary transition-colors"
        >
          <AppIcon name="Trash2" size={13} /> Discard
        </button>
        <AiButton active={aiOpen} onClick={() => setAiOpen((v) => !v)} />
        {saveFailure ? (
          <span className="text-[10px] text-destructive ml-auto">{saveFailure}</span>
        ) : (
          <span className="text-[10px] text-muted-foreground ml-auto">
            {draftStatus === "saving"
              ? "Saving draft…"
              : draftStatus === "saved"
                ? "Draft saved · sends into this conversation"
                : "Sends into this conversation"}
          </span>
        )}
      </div>
      {/* The slot of a failed send (EM-G3c-2 item 14) */}
      {sendError && (
        <p role="alert" className="mt-1.5 text-[10px] text-destructive">
          {sendError}
        </p>
      )}
    </div>
  );
}
