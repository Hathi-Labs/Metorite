"use client";

import Button from "@/components/ui/Button";
import AppIcon, { themedIcon } from "@/components/Icon";
import { useState, useEffect, useRef } from "react";
import { Email } from "../lib/types";
import { fullDateLabel, initials, buildOptimisticSent, bodyMatchKey } from "../lib/utils";
import { useEmailStore, isRealFolder, foldersInScope } from "../lib/emailStore";
import {
  fromWarning, mailboxOf, ownAddresses, replyRecipients, sendBlocked, swapSignature,
} from "../lib/mailbox";
import { getSentFrom } from "../lib/api";
import { FromRow } from "./FromRow";
import { MailboxChip, mailboxLabel } from "./MailboxChip";
import {
  fetchFullBody, getEmail, listThread, createRule,
  fileToSendAttachment,
  type SendAttachment, type ArtifactAttachmentRef,
} from "../lib/api";
import { useDraftSession } from "../lib/useDraftSession";
import { ArtifactAttachPicker } from "./ArtifactAttachPicker";
import { ComposerQuote, AiButton } from "./ComposerAI";
import { DraftAssistant } from "./DraftAssistant";
import { MessageContent } from "./MessageContent";
import { AttachmentList } from "./AttachmentList";
import { getSignatureText, seededBody, stripSignature } from "../lib/signature";
import { RecipientInput } from "./RecipientInput";
import { ConversationView, DraftCard, isDraftEmail } from "./ConversationView";
import { ContactTrigger, RecipientList } from "./ContactCard";
import { LabelMenu } from "./LabelMenu";
import { LabelChip } from "./LabelChip";
import { MessageTimelineModal } from "./MessageTimelineModal";
import { useViewMode } from "@/components/ViewModeProvider";
import {
  FILES_TOO_LARGE, autosaveWait, createAutosave, draftsToDiscard, failedSaveStatus,
  draftToUpdate, holdPick, pickProblem, saveFailureText, sendFailureText, supersededDraft,
  type DraftHandOver, type DraftStatus, type SavedDraft,
} from "../lib/draftAutosave";

interface EmailDetailProps {
  email: Email | null;
}

export function EmailDetail({ email }: EmailDetailProps) {
  const {
    updateEmail, deleteEmail, openCompose, hydrateEmail, folders,
    accounts, selectedAccountId, sendEmail, saveDraft, sendDraft,
    viewerCommand, setViewerCommand, triggerSync, softRefresh,
    captureEmailToTasks, authErrors, viewAll,
  } = useEmailStore();
  // The mailbox of the open mail. Every act on it runs there: the signature,
  // the thread, the drafts, the AI draft and the send. The selected view never
  // decides it (EM-T8a, D-EM-19, MB-2).
  const mailboxId = mailboxOf(email, selectedAccountId);
  // ── The sending mailbox of the inline reply (EM-T8c, D-EM-20) ──
  // It starts on the mailbox of the mail. The From row can pick another one
  // for this mail; a new reply (startReply) starts on the mail's mailbox again.
  const [fromPick, setFromPick] = useState<{ mail: string; id: string } | null>(null);
  const fromId = fromPick && fromPick.mail === email?.id ? fromPick.id : mailboxId;
  // From another mailbox the reply goes as new mail: the provider cannot
  // answer a mail of another mailbox, and the gateway refuses it (MB-6).
  const sameConversation = fromId === mailboxId;
  // Drafts of an old mailbox, deleted once the new mailbox has its own copy.
  const staleDraftsRef = useRef<string[]>([]);
  // The From of NOW, for a save that started before a change of From.
  const liveFromRef = useRef(fromId);
  useEffect(() => {
    liveFromRef.current = fromId;
  }, [fromId]);
  /** Delete each draft of an old mailbox. Call it only once the new mailbox
   *  holds the message, or the member could lose it. The list empties in
   *  place, so a save that holds the same list cannot delete a draft twice. */
  const dropDrafts = (list: string[]) => {
    for (const id of list.splice(0)) void deleteEmail(id);
  };
  const dropStaleDrafts = () => dropDrafts(staleDraftsRef.current);
  const [usualSender, setUsualSender] = useState<Record<string, string>>({});
  const { isMobile } = useViewMode();
  const [starred, setStarred] = useState(email?.isStarred ?? false);
  const [read, setRead] = useState(email?.isRead ?? true);
  const [flagged, setFlagged] = useState(email?.isFlagged ?? false);
  const [showMoreMenu, setShowMoreMenu] = useState(false);
  const [showTimeline, setShowTimeline] = useState(false);
  const [showMoveMenu, setShowMoveMenu] = useState(false);
  const [showLabelMenu, setShowLabelMenu] = useState(false);
  const [replyMode, setReplyMode] = useState<"reply" | "reply-all" | "forward" | null>(
    null
  );
  // Which message in the thread the open composer is replying to (Outlook lets
  // you reply to any message in a conversation, not just the latest).
  const [replyTargetId, setReplyTargetId] = useState<string | null>(null);
  const [replyBody, setReplyBody] = useState("");
  // The quoted trailing email, kept OUT of the editable textarea so it's never
  // edited (or fed to the AI drafter); reattached to the body on send.
  const [replyQuote, setReplyQuote] = useState("");
  const [replyTo, setReplyTo] = useState("");
  const [replyCc, setReplyCc] = useState("");
  const [replyBcc, setReplyBcc] = useState("");
  // Cc/Bcc rows are shown for Reply All (and Forward) and hidden for a
  // sender-only Reply; the toggle below flips this so the fields appear/vanish
  // with the reply mode. A manual reveal is offered when they're hidden.
  const [showReplyCc, setShowReplyCc] = useState(false);
  // AI draft/improve bar (sparkles button in the composer footer).
  const [aiOpen, setAiOpen] = useState(false);
  const [aiInstruction, setAiInstruction] = useState("");
  // The account signature's plain text — it lives IN the reply body (seeded on
  // open, no separate card); kept here to judge whether the user has typed
  // anything beyond it (the AI bar's Draft-vs-Improve).
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
  const [replyAttachments, setReplyAttachments] = useState<SendAttachment[]>([]);
  const [replyArtifacts, setReplyArtifacts] = useState<ArtifactAttachmentRef[]>([]);
  const [sendErr, setSendErr] = useState<string | null>(null);
  // One inline send at a time (EM-T10 item 7, EM-G3c-2-f10). The ref is the
  // guard, because a second click or Ctrl+Enter can come before a render.
  // The state draws the Send button as loading.
  const sendingRef = useRef(false);
  const [sending, setSending] = useState(false);
  // ── Auto-save (Gmail-style): the reply persists as a Drafts message as you
  //    type, so closing the composer never loses it. draftIdRef holds the local
  //    id of the saved draft so repeated saves update it in place (no dupes). ──
  const draftIdRef = useRef<string | null>(null);
  // The resolved message object the composer is replying to — kept in a ref so
  // the auto-save effect / send handlers (which sit above the early return) read
  // the live target without re-subscribing. Set each render once `view` exists.
  const replyTargetRef = useRef<Email | null>(null);
  // The reply/forward composer block — scrolled into view when opened from a
  // conversation card so the draft box isn't off-screen below a long thread.
  const composerRef = useRef<HTMLDivElement>(null);
  const replyDirty = useRef(false);
  // The AI drafting session for this reply: live backend steps + the revision
  // history of each refine round.
  const ai = useDraftSession({
    onBody: (next) => {
      replyDirty.current = true;
      setReplyBody(next);
    },
    onError: setSendErr,
  });
  const [draftStatus, setDraftStatus] = useState<DraftStatus>("idle");
  // The pending autosave of the reply. A close, a switch to another mail and
  // an unmount run it at once (EM-G3c-2 item 11). Each reply is a session, so
  // a save that ends after the reply changed leaves the new reply alone.
  const [autosave] = useState(() => createAutosave());
  const replySessionRef = useRef(0);
  // The draft that the last autosave saved. A save of a reply that ended
  // reads its draft here, never in `draftIdRef` (EM-G3c-2 review round 1).
  const lastSaveRef = useRef<SavedDraft | null>(null);
  // `hasAttachments` of the row that the last save returned (EM-G3c-2 item 12).
  const replyHasFileRef = useRef(false);
  // The bytes of the picks that are being read (EM-G3c-3 item 5).
  const readingRef = useRef(0);
  const [loadingFullBody, setLoadingFullBody] = useState(false);
  const [fullBodyText, setFullBodyText] = useState<string | null>(null);
  // Full message detail (body + attachments) fetched lazily on selection. The
  // list row often carries an empty body (Outlook syncs headers only), so we
  // always fetch the authoritative copy from the gateway when an email opens.
  const [detail, setDetail] = useState<Email | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  // The full conversation (all messages sharing this thread_id), if any.
  const [thread, setThread] = useState<Email[] | null>(null);
  // Just-sent replies shown optimistically until the provider sync mirrors the
  // real copy (kept in a ref so the periodic refetch can re-merge them).
  const optimisticSentRef = useRef<Email[]>([]);

  // Merge any still-unsynced optimistic sent replies into a freshly-fetched
  // thread, dropping the ones the real synced message now covers.
  const mergeThread = (fetched: Email[]): Email[] => {
    const pend = optimisticSentRef.current.filter((o) => {
      const key = bodyMatchKey(o.bodyText);
      if (!key) return false;
      const covered = fetched.some(
        (f) =>
          (f.folder || "").toLowerCase() === "sent" &&
          bodyMatchKey(f.bodyText).includes(key)
      );
      return !covered;
    });
    optimisticSentRef.current = pend;
    return pend.length ? [...fetched, ...pend] : fetched;
  };

  // After a reply is sent: show it instantly, then pull the real copy (sync +
  // a few staged thread/list refetches so the conversation and the Reply Zero
  // chip update within a couple of seconds, not on the next 20s tick).
  const refreshThreadAfterSend = (sent?: Email) => {
    if (sent) {
      optimisticSentRef.current = [...optimisticSentRef.current, sent];
      setThread((cur) => mergeThread(cur ?? []));
    }
    const acct = mailboxId ?? undefined;
    // A reply from another mailbox lands there, so that mailbox syncs too.
    if (fromId && fromId !== acct) void triggerSync(fromId);
    const threadId = email?.threadId;
    if (acct) void triggerSync(acct);
    if (!threadId) return;
    [1500, 4000, 8000].forEach((d) =>
      setTimeout(() => {
        listThread(acct, threadId)
          .then((t) => setThread(mergeThread(t)))
          .catch(() => {});
      }, d)
    );
    setTimeout(() => void softRefresh(), 5000);
  };

  // Create an archive rule for this sender, then archive the open message.
  const blockSender = async () => {
    if (!email) return;
    const sender = email.from.email;
    const accountId = mailboxId;
    if (!sender || !accountId) return;
    try {
      await createRule({
        account_id: accountId,
        name: `Block ${sender}`.slice(0, 60),
        instructions: "",
        from_pattern: sender,
        enabled: true,
        automated: true,
        run_on_threads: false,
        conditional_operator: "OR",
        actions: [{ type: "ARCHIVE" }],
      });
    } catch {
      /* best-effort — still archive below */
    }
    updateEmail(email.id, { folder: "archive" });
  };

  // Download the open message as a .eml file.
  const downloadEml = () => {
    if (!email) return;
    const body = detail?.bodyText || email.bodyText || fullBodyText || "";
    const eml =
      `From: ${email.from.name} <${email.from.email}>\n` +
      `To: ${email.to.map((t) => t.email).join(", ")}\n` +
      `Subject: ${email.subject}\n` +
      `Date: ${email.receivedAt}\n\n` +
      body;
    const url = URL.createObjectURL(
      new Blob([eml], { type: "message/rfc822" })
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(email.subject || "email")
      .replace(/[^a-z0-9]+/gi, "_")
      .slice(0, 40)}.eml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Background refresh of the OPEN conversation (~20s) so an assistant-created
  // draft, or a reply that just synced from upstream, appears without reopening
  // the thread. Re-fetching keeps existing cards (keyed by message id — incl. a
  // draft you're editing) and only adds new ones. Pauses when the tab is hidden.
  useEffect(() => {
    if (!email?.threadId) return;
    const threadId = email.threadId;
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      listThread(mailboxId ?? undefined, threadId)
        .then((t) => setThread(mergeThread(t)))
        .catch(() => {});
    };
    const id = setInterval(tick, 20000);
    return () => clearInterval(id);
  }, [email?.threadId, mailboxId]);

  // Fetch full content whenever the selected email changes.
  useEffect(() => {
    // A switch to another mail runs the pending save of the reply at once,
    // while `draftIdRef` still names its draft (EM-G3c-2 item 11). The
    // cleanup of the autosave effect ran first and held it.
    autosave.flush();
    replySessionRef.current += 1;
    replyHasFileRef.current = false;
    if (!email) {
      setDetail(null);
      setThread(null);
      return;
    }
    let cancelled = false;
    setDetail(null);
    setThread(null);
    optimisticSentRef.current = []; // new conversation — drop pending sent cards
    setFullBodyText(null);
    setReplyMode(null);
    setReplyTargetId(null);
    setReplyQuote("");
    setAiOpen(false);
    setAiInstruction("");
    draftIdRef.current = null;
    replyDirty.current = false;
    setDraftStatus("idle");
    // Pull the whole conversation so we can show a Gmail-style thread view.
    if (email.threadId) {
      listThread(mailboxId ?? undefined, email.threadId)
        .then((t) => { if (!cancelled) setThread(mergeThread(t)); })
        .catch(() => { if (!cancelled) setThread(null); });
    }
    const needsBody = !email.bodyHtml && !email.bodyText;
    const needsAttachments = email.hasAttachments && (!email.attachments || email.attachments.length === 0);
    if (!needsBody && !needsAttachments) {
      setDetail(email);
      return;
    }
    setLoadingDetail(true);
    getEmail(email.id)
      .then((full) => {
        if (!cancelled) {
          setDetail(full);
          hydrateEmail(full);
        }
      })
      .catch(() => {
        if (!cancelled) setDetail(email); // fall back to list row
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [email?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Gmail-style auto-save ──
  // Debounce-persist the open reply/forward as a Drafts message once the user
  // edits it. Creates the draft on the first save (threaded for reply/reply-all)
  // and updates the same one thereafter, so closing the box never loses work.
  // NOTE: must stay ABOVE the `if (!email) return` early-return so the hook is
  // called on every render (moving it below crashes with a hooks-order error).
  useEffect(() => {
    // ignore the prefilled quote — wait for edits
    if (!replyMode || !fromId || !email || !replyDirty.current) {
      autosave.cancel();
      return;
    }
    const toArr = replyTo.split(",").map((s) => s.trim()).filter(Boolean);
    if (!replyBody.trim() && toArr.length === 0) {
      autosave.cancel();
      return;
    }
    // Each save carries the Cc and the Bcc of the reply. The gateway writes
    // the lists that it gets, so a save without them cleared both on Outlook,
    // and the send read the cleared row (EM-G3c-2 review round 2).
    const ccArr = replyCc.split(",").map((s) => s.trim()).filter(Boolean);
    const bccArr = replyBcc.split(",").map((s) => s.trim()).filter(Boolean);
    const isForward = replyMode === "forward";
    const target = replyTargetRef.current ?? email;
    const subj0 = target.subject || "";
    const subject = isForward
      ? (subj0.startsWith("Fwd:") ? subj0 : `Fwd: ${subj0}`)
      : (subj0.startsWith("Re:") ? subj0 : `Re: ${subj0}`);
    // Persist the full outgoing message — new text plus the quoted trailing chain.
    const body = replyQuote
      ? `${replyBody.replace(/\s+$/, "")}\n\n${replyQuote}`
      : replyBody;
    const savingFrom = fromId;
    const session = replySessionRef.current;
    // The drafts that this reply left in another mailbox (EM-G3c-2 review
    // round 1). A save that ends after the reply ended still deletes them.
    const stale = staleDraftsRef.current;
    autosave.schedule(async () => {
      // The save runs after the save before it settled, so it reads the
      // draft id now. A first save that made the draft gave it the id.
      const draftId = draftToUpdate(
        { session, from: savingFrom },
        { session: replySessionRef.current, draftId: draftIdRef.current },
        lastSaveRef.current,
      );
      try {
        if (replySessionRef.current === session) setDraftStatus("saving");
        const saved = await saveDraft({
          accountId: savingFrom,
          draftId: draftId ?? undefined,
          // Reply/Reply-All thread onto the target message; Forward is
          // standalone, and so is a reply from another mailbox (EM-T8c).
          replyToMessageId: isForward || !sameConversation ? undefined : target.id,
          to: toArr,
          cc: ccArr,
          bcc: bccArr,
          subject,
          body,
        });
        // The reply or the mail changed while this save ran. The save holds
        // the message, so the drafts that its reply left can go, unless the
        // save wrote one of them. It sets nothing of the new reply.
        if (replySessionRef.current !== session) {
          // A change of From left the draft that this reply saved last in the
          // old mailbox, and no list holds it (EM-G3c-3 item 4).
          const superseded = supersededDraft({ session, from: savingFrom }, lastSaveRef.current, saved.id);
          if (superseded && !stale.includes(superseded)) void deleteEmail(superseded);
          lastSaveRef.current = { session, from: savingFrom, id: saved.id };
          if (!stale.includes(saved.id)) dropDrafts(stale);
          return;
        }
        if (liveFromRef.current !== savingFrom) {
          // The From changed while this save ran: its draft belongs to the
          // old mailbox (EM-T8c review).
          staleDraftsRef.current.push(saved.id);
          setDraftStatus("idle");
          return;
        }
        draftIdRef.current = saved.id;
        replyHasFileRef.current = saved.hasAttachments;
        lastSaveRef.current = { session, from: savingFrom, id: saved.id };
        // A provider draft cannot move between mailboxes (§11.6 case 7).
        dropStaleDrafts();
        setDraftStatus("saved");
      } catch (err) {
        // The next edit tries again (EM-G3c-2 item 13).
        if (replySessionRef.current === session) setDraftStatus(failedSaveStatus(err));
      }
    }, autosaveWait(
      accounts.find((a) => a.id === savingFrom)?.provider,
      replyHasFileRef.current,
    ), session);
    // Stop the timer and keep the save, so a close or a switch can flush it.
    return () => autosave.hold();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replyBody, replyQuote, replyTo, replyCc, replyBcc, replyMode, fromId, email?.id]);

  // An unmount runs the pending save at once (EM-G3c-2 item 11).
  useEffect(() => () => autosave.flush(), [autosave]);

  // Which mailbox last wrote to each recipient, for the From warning. Only
  // for a member with two or more mailboxes, after a short pause.
  useEffect(() => {
    if (!replyMode || accounts.length < 2) return;
    const list = [...replyTo.split(","), ...replyCc.split(",")]
      .map((s) => s.trim()).filter(Boolean);
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
  }, [replyTo, replyCc, replyMode, accounts.length]);

  // Bridge for the desktop unified toolbar: it issues a transient store command
  // (reply/forward/block/download) that this viewer executes via the live
  // handlers captured in the ref below (kept in a ref so this effect — which
  // must sit above the early return — needn't reference them before they exist).
  const cmdRef = useRef<{
    reply: (m: "reply" | "reply-all" | "forward") => void;
    block: () => void;
    download: () => void;
  }>({ reply: () => {}, block: () => {}, download: () => {} });
  // Draft-from-dashboard: "reply-ai" opens the composer then auto-runs the AI
  // draft. The two steps can't be chained synchronously — startReply sets state
  // (recipients, mode) that runAiDraft reads, and that state isn't live until
  // the next render. So we open the composer here and bump a nonce; an effect
  // below fires the draft once the fresh state has committed. Routed through a
  // ref because runAiDraft is defined after this hook (which must sit above the
  // early return).
  const runAiDraftRef = useRef<() => void>(() => {});
  // Seeds the auto-draft's AI instruction: "" for a plain reply-ai draft, a
  // nudge prompt for "nudge" (a follow-up on a thread we're waiting on).
  // Read + cleared by runAiDraft — a ref, not state, so it's live the moment
  // the nonce effect fires without another render.
  const autoDraftInstructionRef = useRef("");
  const [autoDraftTick, setAutoDraftTick] = useState(0);
  useEffect(() => {
    if (!viewerCommand) return;
    const h = cmdRef.current;
    if (viewerCommand === "block") h.block();
    else if (viewerCommand === "download") h.download();
    else if (viewerCommand === "reply-ai") {
      autoDraftInstructionRef.current = "";
      h.reply("reply");
      setAutoDraftTick((t) => t + 1);
    } else if (viewerCommand === "nudge") {
      // A follow-up on a thread we're waiting on. Reply-all (not reply): the
      // last message is usually OURS, so a plain reply would address us — reply
      // -all keeps the original recipients (the people we're waiting on).
      autoDraftInstructionRef.current =
        "Write a brief, friendly follow-up. I haven't heard back on this yet " +
        "and want to gently nudge for a reply. Keep it short and polite.";
      h.reply("reply-all");
      setAutoDraftTick((t) => t + 1);
    } else h.reply(viewerCommand);
    setViewerCommand(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerCommand]);
  useEffect(() => {
    if (autoDraftTick === 0) return; // never on mount, only on an actual trigger
    runAiDraftRef.current();
  }, [autoDraftTick]);

  // Keep state in sync when email changes
  if (email && starred !== email.isStarred) setStarred(email.isStarred);
  if (email && read !== email.isRead) setRead(email.isRead);
  if (email && flagged !== email.isFlagged) setFlagged(email.isFlagged);

  if (!email) {
    return (
      <div className="flex flex-col h-full items-center justify-center text-muted-foreground gap-3">
        <div className="w-12 h-12 rounded-full bg-secondary flex items-center justify-center">
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <rect x="2" y="4" width="20" height="16" rx="2" />
            <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
          </svg>
        </div>
        <p className="text-sm">Select an email to read</p>
      </div>
    );
  }

  // Render the fullest copy we have (the lazily-fetched detail, or the list row).
  // `detail` resets only after the first render of a new mail, so it can still
  // hold the last mail. Read it only when it is this mail (EM-T10 item 5, C2).
  const view: Email = detail?.id === email.id ? detail : email;

  // The message the composer replies to. Defaults to the open message; a
  // conversation card can target any message in the thread (Outlook parity).
  const replyTarget: Email =
    (replyTargetId ? thread?.find((m) => m.id === replyTargetId) : undefined) ??
    view;
  replyTargetRef.current = replyTarget;

  // "Not saved" or "Too large to save" after a failed save (EM-G3c-2 item 13).
  const saveFailure = saveFailureText(draftStatus);

  const replyLabel =
    replyMode === "forward"
      ? "Forward"
      : replyMode === "reply-all"
        ? "Reply All"
        : "Reply";

  // The address of the mailbox that sends (the mailbox of the mail), for the
  // optimistic sent row. Reply-all leaves out EVERY address of the member, not
  // only this one (D-EM-27, MB-7).
  const mailboxAccount = accounts.find((a) => a.id === mailboxId);
  const sendingAddress = mailboxAccount?.emailAddress?.toLowerCase();
  const own = ownAddresses(accounts);

  /** Open the inline composer with recipients + a quoted body prefilled.
   *  `target` is the message being replied to (defaults to the open message);
   *  a conversation card passes the specific message the user chose. */
  const startReply = (
    mode: "reply" | "reply-all" | "forward",
    target?: Email
  ) => {
    const src = target ?? view;
    // A new reply closes the one before, so its pending save runs at once
    // (EM-G3c-2 item 11).
    autosave.flush();
    replySessionRef.current += 1;
    replyHasFileRef.current = false;
    setReplyTargetId(src.id);
    setFromPick(null);
    staleDraftsRef.current = [];
    setSendErr(null);
    // New reply session: forget any previous draft so we don't update it.
    draftIdRef.current = null;
    replyDirty.current = false;
    setDraftStatus("idle");
    setReplyBcc("");
    setReplyAttachments([]);
    setReplyArtifacts([]);
    setAiOpen(false);
    setAiInstruction("");
    // HTML-only mail (e.g. Outlook) has no bodyText — fall back to the snippet.
    const quoteSrc = src.bodyText || src.snippet || "";
    // The editable box starts with just the SIGNATURE (Gmail-style — it's part
    // of the draft body now, visible upstream too, not a separate card); the
    // caret stays at the top so typing lands above it. The quoted trailing
    // chain is kept separate in `replyQuote`, shown collapsed below the box and
    // reattached on send — so it can never be edited or AI-rewritten by mistake.
    setReplyBody("");
    void getSignatureText(mailboxId).then((sig) => {
      // Seed only while the box is still untouched — never clobber typing.
      if (sig) setReplyBody((prev) => (prev.trim() ? prev : seededBody(sig)));
    });
    // Reveal Cc/Bcc for Reply All / Forward; hide them for a sender-only Reply.
    setShowReplyCc(mode !== "reply");
    if (mode === "forward") {
      setReplyTo("");
      setReplyCc("");
      setReplyQuote(
        `---------- Forwarded message ----------\n` +
        `From: ${src.from.name} <${src.from.email}>\n` +
        `Date: ${src.receivedAt}\nSubject: ${src.subject}\n\n${quoteSrc}`
      );
    } else {
      const { to, cc } = replyRecipients(src, mode, own, sendingAddress);
      setReplyTo(to.join(", "));
      setReplyCc(cc.join(", "));
      setReplyQuote(
        `On ${src.receivedAt}, ${src.from.name} wrote:\n> ` +
        quoteSrc.replace(/\n/g, "\n> ")
      );
    }
    setReplyMode(mode);
    // Bring the composer into view (it renders below a possibly-long thread).
    setTimeout(
      () => composerRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }),
      60
    );
  };

  /** Switch reply mode without resetting the body/draft — only rebuilds recipients. */
  const switchReplyMode = (mode: "reply" | "reply-all") => {
    const src = replyTargetRef.current ?? view;
    const { to, cc } = replyRecipients(src, mode, own, sendingAddress);
    setReplyTo(to.join(", "));
    setReplyCc(cc.join(", "));
    // Reply All reveals Cc/Bcc; narrowing to Reply hides them (and drops Bcc).
    setShowReplyCc(mode === "reply-all");
    if (mode === "reply") setReplyBcc("");
    setReplyMode(mode);
    replyDirty.current = true;
  };

  const replySubject = () => {
    const subj = replyTarget.subject || "";
    return replyMode === "forward"
      ? subj.startsWith("Fwd:")
        ? subj
        : `Fwd: ${subj}`
      : subj.startsWith("Re:")
        ? subj
        : `Re: ${subj}`;
  };

  const resetReplySession = () => {
    // A discard and a send end the reply. Each drained the chain before, and
    // the cancel drops a save that an edit scheduled since then.
    autosave.cancel();
    replySessionRef.current += 1;
    replyHasFileRef.current = false;
    draftIdRef.current = null;
    replyDirty.current = false;
    setDraftStatus("idle");
    setReplyMode(null);
    setReplyBody("");
    setReplyQuote("");
    setReplyTo("");
    setReplyCc("");
    setReplyBcc("");
    setShowReplyCc(false);
    setReplyAttachments([]);
    setReplyArtifacts([]);
    setAiOpen(false);
    setAiInstruction("");
    ai.reset(); // a new reply starts a fresh drafting session
  };

  /** Discard the reply and its auto-saved draft. The X keeps the draft
   *  instead. The reply closes at once. The chain drains first, so no save
   *  that waited writes the draft again, and a first save that runs gives its
   *  id to the delete (EM-G3c-2 review round 2). The delete takes each draft
   *  of the reply, in each mailbox (EM-G3c-3 item 3). */
  const discardReply = async () => {
    const session = replySessionRef.current;
    const stale = staleDraftsRef.current;
    const drained = autosave.drain(session);
    setSendErr(null);
    resetReplySession();
    await drained;
    // The reset ended the session, so this reads the last save of the reply,
    // in the mailbox that it saved to, not the From of now.
    const ids = draftsToDiscard(
      session,
      { session: replySessionRef.current, draftId: draftIdRef.current },
      lastSaveRef.current,
      stale.splice(0),
    );
    for (const id of ids) void deleteEmail(id);
  };

  /** The full outgoing body: the user's new text plus the quoted trailing chain. */
  const composedReply = (newBody: string) =>
    replyQuote ? `${newBody.replace(/\s+$/, "")}\n\n${replyQuote}` : newBody;

  /** Draft or refine the reply with AI — operates on the NEW text only (the
   *  quoted trailing chain is never sent). Backend steps stream into the
   *  panel, draft text lands live, and the panel stays open so the next
   *  instruction refines this draft instead of starting over. */
  const runAiDraft = async () => {
    if (!fromId || ai.busy) return;
    setSendErr(null);
    const target = replyTargetRef.current ?? email;
    const toArr = replyTo.split(",").map((s) => s.trim()).filter(Boolean);
    // A dashboard nudge seeds a follow-up instruction (consumed once); a
    // manual AI draft uses whatever the user typed in the AI box.
    const seeded = autoDraftInstructionRef.current;
    autoDraftInstructionRef.current = "";
    const instruction = seeded || aiInstruction.trim();
    const draft = await ai.run(
      {
        accountId: fromId,
        body: replyBody, // NEW text only — the quote is excluded by design
        instruction,
        mode: replyMode === "forward" ? "forward" : "reply",
        // The drafter reads the thread only inside the mailbox of the mail.
        messageId: sameConversation ? target.id : undefined,
        to: toArr,
        subject: replyTarget.subject,
      },
      { instruction },
    );
    // The panel stays open on success: the next instruction refines this draft.
    if (draft) setAiInstruction("");
  };

  /** Send the reply/forward. If it was auto-saved as a draft we send that draft
   *  natively (Drafts → Sent, no duplicate); otherwise we send a fresh message. */
  const handleInlineSend = async () => {
    // A send runs already: the click or the Ctrl+Enter does nothing (f10).
    if (sendingRef.current) return;
    if (!email) return;
    // An old send error must not hide a later "Not saved" (review round 1).
    setSendErr(null);
    if (!fromId) {
      setSendErr("This mail has no mailbox to send from");
      return;
    }
    const fromAccount = accounts.find((a) => a.id === fromId);
    if (sendBlocked(fromAccount, authErrors)) {
      setSendErr(`Reconnect ${fromAccount ? mailboxLabel(fromAccount) : "this mailbox"} to send from it.`);
      return;
    }
    const toArr = replyTo.split(",").map((s) => s.trim()).filter(Boolean);
    if (toArr.length === 0) {
      setSendErr("Add at least one recipient");
      return;
    }
    const ccArr = replyCc.split(",").map((s) => s.trim()).filter(Boolean);
    const bccArr = replyBcc.split(",").map((s) => s.trim()).filter(Boolean);
    const isForward = replyMode === "forward";
    const target = replyTargetRef.current ?? email;
    // The send starts here, after the early returns and before the drain, so
    // the Send button shows it while the drain waits (EM-T10 item 7).
    sendingRef.current = true;
    setSending(true);
    try {
      // The send carries the last edit, so each queued autosave goes. A save
      // that runs settles first: a first save gives its draft id, and no older
      // text without Cc lands after this save (EM-G3c-2 review round 2). A
      // save of a reply that the member closed still runs (EM-G3c-3 item 1).
      await autosave.drain(replySessionRef.current);
      // Native draft-send now carries Cc/Bcc AND attachments (all stored on the
      // provider draft), so whenever there's a draft OR attachments we save the
      // draft with everything and send it natively (Drafts → Sent, no duplicate,
      // reply stays threaded). A plain reply with no draft/attachments still
      // sends fresh.
      const hasAtt = replyAttachments.length > 0 || replyArtifacts.length > 0;
      // A change of From with an old draft takes the draft path too: it awaits
      // the real send, so the old draft goes only after the send (§11.6
      // case 8). The direct path returns before the send runs.
      if (draftIdRef.current || hasAtt || staleDraftsRef.current.length > 0) {
        const saved = await saveDraft({
          accountId: fromId,
          draftId: draftIdRef.current ?? undefined,
          replyToMessageId: isForward || !sameConversation ? undefined : target.id,
          to: toArr,
          cc: ccArr,
          bcc: bccArr,
          subject: replySubject(),
          body: composedReply(replyBody),
          attachments: replyAttachments.length ? replyAttachments : undefined,
          artifacts: replyArtifacts.length ? replyArtifacts : undefined,
        });
        // When the send fails, the next autosave updates this draft, and it
        // holds the files now (EM-G3c-2 item 12).
        if (saved.id === draftIdRef.current) replyHasFileRef.current = saved.hasAttachments;
        await sendDraft(fromId, saved.id);
        dropStaleDrafts();
      } else {
        sendEmail({
          accountId: fromId,
          to: toArr,
          cc: ccArr.length ? ccArr : undefined,
          bcc: bccArr.length ? bccArr : undefined,
          subject: replySubject(),
          bodyText: composedReply(replyBody),
          replyToMessageId: isForward || !sameConversation ? undefined : target.providerMessageId,
        });
      }
    } catch (e) {
      // A 413 shows "This mail is too large to send." (EM-G3c-2 item 14).
      setSendErr(sendFailureText(e));
      return;
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
    // Show the reply in the conversation at once, then pull the real synced copy.
    // A reply from another mailbox starts a conversation THERE, so it does not
    // join this thread.
    const sent = email.threadId && sameConversation
      ? buildOptimisticSent({
          accountId: fromId,
          threadId: email.threadId,
          fromEmail: sendingAddress || "",
          to: toArr,
          cc: ccArr,
          subject: replySubject(),
          bodyText: composedReply(replyBody),
          hasAttachments:
            replyAttachments.length > 0 || replyArtifacts.length > 0,
        })
      : undefined;
    resetReplySession();
    refreshThreadAfterSend(sent);
  };

  /** Read picked files into base64 and append them to the reply's attachments.
   *  A pick that makes the files pass 7.5 MB is refused whole (EM-G3c-2 item 10).
   *  The limit counts the picks that are being read too (EM-G3c-3 item 5). */
  const addReplyFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const picked = Array.from(files);
    const problem = pickProblem(replyAttachments, picked, readingRef.current);
    if (problem) {
      setSendErr(problem);
      return;
    }
    replyDirty.current = true;
    const release = holdPick(readingRef, picked);
    try {
      const added = await Promise.all(picked.map(fileToSendAttachment));
      setReplyAttachments((prev) => [...prev, ...added]);
      setSendErr((prev) => (prev === FILES_TOO_LARGE ? null : prev));
    } catch {
      setSendErr("Couldn't read one of the attachments");
    } finally {
      release();
    }
  };

  /** Change the sending mailbox of the inline reply. The signature follows
   *  it, and a saved draft moves to the new mailbox on the next save. */
  const changeFrom = async (next: string) => {
    if (!email || !next || next === fromId) return;
    const [oldSig, newSig] = await Promise.all([
      getSignatureText(fromId), getSignatureText(next),
    ]);
    if (draftIdRef.current) {
      staleDraftsRef.current.push(draftIdRef.current);
      draftIdRef.current = null;
    }
    // The next save makes a new draft in the new mailbox, with no file.
    replyHasFileRef.current = false;
    replyDirty.current = true;
    setReplyBody((prev) => swapSignature(prev, oldSig, newSig));
    setFromPick({ mail: email.id, id: next });
  };

  /** Hand the current draft off to the full composer (Cc/Bcc, attachments). */
  const popOutToComposer = () => {
    const session = replySessionRef.current;
    const from = fromId ?? "";
    const stale = staleDraftsRef.current;
    // The full composer takes over the draft, so the chain of the reply
    // drains: no save of the reply writes the draft after the hand-over. A
    // save that runs settles first, so a first save gives its draft id. A
    // flush here would make two drafts (EM-G3c-2 review rounds 1 and 2).
    const drained = autosave.drain(session);
    setReplyMode(null);
    // The draft of the reply goes to the full composer when the drain
    // settles. The composer opens now, so a New in that gap cannot take the
    // composer first (EM-G3c-3 item 6, EM-G3c-2-f8).
    const handOver: Promise<DraftHandOver> = drained.then(() => {
      const draftId = draftToUpdate(
        { session, from },
        { session: replySessionRef.current, draftId: draftIdRef.current },
        lastSaveRef.current,
      );
      // The new mailbox holds the reply once its own draft exists, so the
      // drafts of an old mailbox can go. Without one they stay, as a copy.
      if (draftId) dropDrafts(stale);
      // The full composer has no Bcc row, so its saves would clear a Bcc. A
      // reply with a Bcc keeps its draft, and the composer makes its own.
      const kept = draftId && !replyBcc.trim() ? draftId : undefined;
      return { draftId: kept, draftHasFile: kept ? replyHasFileRef.current : undefined };
    });
    openCompose({
      // The pop-out opens on the mailbox of the mail (EM-T8a, MB-2), and it
      // keeps the From that the member chose (EM-T8c review). The composer
      // drops the reply target itself while the From is another mailbox.
      accountId: mailboxId ?? undefined,
      fromAccountId: fromId && fromId !== mailboxId ? fromId : undefined,
      to: replyTo,
      // The composer saves the draft that it updates with this Cc.
      cc: replyCc,
      subject: replySubject(),
      replyToBody: replyBody,   // the typed new text
      // The composer opens dirty in each case, so its first save writes the
      // text that the member sees, at the cost of one save. The drain cannot
      // tell a save that failed, and this closure cannot see `draftStatus`
      // change (EM-G3c-3 item 2, EM-G3c-2-f9).
      unsavedEdit: true,
      // The composer updates the draft of the reply, in the mailbox of the
      // From above, and makes no second draft (EM-G3c-2 review round 2).
      handOver,
      quote: replyQuote,        // the collapsed trailing chain
      replyToMessageId:
        replyMode === "forward" ? undefined : replyTarget.providerMessageId,
      // Local id so the popped-out "Draft with AI" keeps the reply context
      // (a forward isn't a reply, so no context to carry).
      messageId: replyMode === "forward" ? undefined : replyTarget.id,
    });
  };

  // Keep the command bridge pointed at the live handlers (runs each render).
  cmdRef.current = { reply: startReply, block: blockSender, download: downloadEml };
  // Point the auto-draft ref at the current-render closure so the nonce effect
  // reads fresh reply state (recipients/mode startReply just set).
  runAiDraftRef.current = runAiDraft;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* ── Main toolbar (MOBILE ONLY — on desktop the unified EmailToolbar
          below the page top bar provides these actions) ── */}
      {isMobile && (
      <div className="flex items-center justify-between px-3 py-2 border-b border-border flex-shrink-0 bg-card">
        {/* Left group */}
        <div className="flex items-center gap-0.5 flex-wrap">
          <TBtn
            icon={themedIcon("ReplyAll")}
            label="Reply All"
            onClick={() => startReply("reply-all")}
            active={replyMode === "reply-all"}
          />
          <TBtn
            icon={themedIcon("Reply")}
            label="Reply"
            onClick={() => startReply("reply")}
            active={replyMode === "reply"}
          />
          <TBtn
            icon={themedIcon("Forward")}
            label="Forward"
            onClick={() => startReply("forward")}
            active={replyMode === "forward"}
          />

          <TBtn
            icon={themedIcon("ListChecks")}
            label="Add to My Tasks"
            onClick={() => {
              if (email) captureEmailToTasks(email.id);
            }}
          />

          <Divider />

          <TBtn icon={themedIcon("Archive")} label="Archive" onClick={() => {
            if (email) updateEmail(email.id, { folder: "archive" });
          }} />
          <TBtn icon={themedIcon("Trash2")} label="Delete" onClick={() => {
            if (email) deleteEmail(email.id);
          }} />
          <div className="relative">
            <TBtn
              icon={themedIcon("FolderInput")}
              label="Move to folder"
              onClick={() => setShowMoveMenu((v) => !v)}
              active={showMoveMenu}
            />
            {showMoveMenu && (
              <>
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setShowMoveMenu(false)}
                />
                <div className="absolute left-0 top-full mt-1 z-20 bg-popover border border-border rounded-lg shadow-xl py-1 w-44 max-h-64 overflow-y-auto">
                  <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                    Move to
                  </div>
                  {foldersInScope(folders, viewAll)
                    .filter((f) => isRealFolder(f.key) && f.key !== email?.folder)
                    .map((f) => (
                      <button
                        key={f.key}
                        className="w-full text-left px-3 py-1.5 text-xs text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
                        onClick={() => {
                          if (email) updateEmail(email.id, { folder: f.key });
                          setShowMoveMenu(false);
                        }}
                      >
                        {f.label}
                      </button>
                    ))}
                </div>
              </>
            )}
          </div>

          <Divider />

          <TBtn
            icon={themedIcon("Flag")}
            label={flagged ? "Unflag" : "Flag"}
            onClick={() => {
              if (email) {
                setFlagged((v) => !v);
                updateEmail(email.id, { isFlagged: !flagged });
              }
            }}
            active={flagged}
          />
          <TBtn
            icon={themedIcon("Star")}
            label={starred ? "Unstar" : "Star"}
            onClick={() => {
              if (email) {
                setStarred((v) => !v);
                updateEmail(email.id, { isStarred: !starred });
              }
            }}
            active={starred}
          />
          <TBtn
            icon={themedIcon("MailOpen")}
            label={read ? "Mark as Unread" : "Mark as Read"}
            onClick={() => {
              if (email) {
                setRead((v) => !v);
                updateEmail(email.id, { isRead: !read });
              }
            }}
            active={!read}
          />
          <div className="relative">
            <TBtn
              icon={themedIcon("Tag")}
              label="Label"
              onClick={() => setShowLabelMenu((v) => !v)}
              active={showLabelMenu}
            />
            {showLabelMenu && email && (
              <>
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setShowLabelMenu(false)}
                />
                <div className="absolute left-0 top-full mt-1 z-20">
                  <LabelMenu email={email} />
                </div>
              </>
            )}
          </div>

          <Divider />

          <TBtn icon={themedIcon("Printer")} label="Print" onClick={() => window.print()} />
        </div>

        {/* More menu */}
        <div className="relative">
          <TBtn
            icon={themedIcon("MoreHorizontal")}
            label="More options"
            onClick={() => setShowMoreMenu((v) => !v)}
            active={showMoreMenu}
          />
          {showMoreMenu && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setShowMoreMenu(false)}
              />
              <div className="absolute right-0 top-full mt-1 z-20 bg-popover border border-border rounded-lg shadow-xl py-1 w-44">
                {[
                  {
                    label: "View activity",
                    run: () => setShowTimeline(true),
                  },
                  {
                    label: "Mark as spam",
                    run: () => updateEmail(email.id, { folder: "junk" }),
                  },
                  {
                    label: "Report phishing",
                    run: () => updateEmail(email.id, { folder: "junk" }),
                  },
                  {
                    label: "Block sender",
                    run: () => blockSender(),
                  },
                  {
                    label: "Download email",
                    run: () => downloadEml(),
                  },
                ].map((item) => (
                  <button
                    key={item.label}
                    className="w-full text-left px-3 py-1.5 text-xs text-foreground/80 hover:text-foreground hover:bg-secondary transition-colors"
                    onClick={() => {
                      item.run();
                      setShowMoreMenu(false);
                    }}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
      )}

      {/* ── Email content ── */}
      <div className="flex-1 overflow-y-auto scrollbar-hide px-6 py-5">
        {/* Subject + status badges */}
        <div className="flex items-start gap-2 mb-4">
          <h2 className="flex-1 text-foreground text-lg font-semibold">
            {email.subject}
          </h2>
          <div className="flex items-center gap-1 flex-shrink-0 mt-1">
            {view.importance === "high" && (
              <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 bg-red-500/15 text-red-400 rounded-full">
                <AppIcon name="AlertTriangle" size={9} /> Important
              </span>
            )}
            {flagged && (
              <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 bg-warning/15 text-warning rounded-full">
                <AppIcon name="Flag" size={9} /> Flagged
              </span>
            )}
            {!read && (
              <span className="text-[9px] px-1.5 py-0.5 bg-primary/15 text-primary rounded-full">
                Unread
              </span>
            )}
          </div>
        </div>

        {/* The mailbox that holds this mail, for a member with two or more
            mailboxes (EM-T8b, §11.4 "Reading pane"). */}
        {mailboxAccount && accounts.length > 1 && (
          <div className="-mt-2 mb-4 flex min-w-0 items-center gap-1.5 text-[11px] text-muted-foreground">
            <span>In</span>
            <MailboxChip account={mailboxAccount} />
            <span className="truncate">· to {mailboxAccount.emailAddress}</span>
          </div>
        )}

        {/* Categories / labels — rendered in their assigned colours */}
        {view.categories && view.categories.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-4">
            {view.categories.map((cat) => (
              <LabelChip
                key={cat}
                name={cat}
                icon
                className="text-[11px] px-2.5 py-1"
              />
            ))}
          </div>
        )}

        {thread && thread.length > 1 ? (
          /* Conversation view — the whole thread, stacked */
          <ConversationView
            messages={thread}
            openedId={email.id}
            onReply={(m, mode) => startReply(mode, m)}
            onSent={refreshThreadAfterSend}
          />
        ) : isDraftEmail(email) ? (
          /* Standalone draft — editable composer. The key mounts a fresh card
             for each draft. Without it, opening draft B after draft A kept A's
             text, and an edit saved it into B (EM-G3c-2-f1). The card gets
             `view`, never the last mail, so it starts with the recipients of
             its own draft (EM-T10 item 5, C2). */
          <DraftCard key={email.id} draft={email} replyTo={view} />
        ) : (
        <>
        {/* Sender info — the avatar, name and every recipient open a contact card */}
        <div className="flex items-start gap-3 mb-6">
          <ContactTrigger
            contact={email.from}
            accountId={email.accountId}
            className="w-9 h-9 rounded-full bg-primary/20 text-primary flex items-center justify-center flex-shrink-0 text-xs font-semibold hover:ring-2 hover:ring-primary/40 tech-transition"
          >
            {initials(email.from.name)}
          </ContactTrigger>
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <ContactTrigger
                contact={email.from}
                accountId={email.accountId}
                className="text-sm font-medium text-foreground hover:text-primary hover:underline tech-transition"
              >
                {email.from.name}
              </ContactTrigger>
              <span className="text-xs text-muted-foreground">
                &lt;{email.from.email}&gt;
              </span>
            </div>
            <div className="mt-0.5">
              <RecipientList
                label="To"
                people={email.to}
                accountId={email.accountId}
              />
            </div>
            <RecipientList
              label="Cc"
              people={email.cc ?? []}
              accountId={email.accountId}
            />
            <div className="text-xs text-muted-foreground">
              {fullDateLabel(email.receivedAt)}
            </div>
          </div>
          {/* Card-level capture: turn this email (with its thread + who's on it)
              into a routed task in My Tasks — always visible with the message. */}
          <button
            type="button"
            onClick={() => captureEmailToTasks(email.id)}
            title="Add to My Tasks — the assistant reads the thread and files a routed task (follow-up / delegated / next action) with a due date if implied."
            className="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-primary transition-colors"
          >
            <AppIcon name="ListChecks" size={14} />
            <span className="hidden sm:inline">Add to My Tasks</span>
          </button>
        </div>

        {/* Body */}
        {loadingDetail ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground py-6">
            <AppIcon name="Loader2" size={14} className="animate-spin" /> Loading message…
          </div>
        ) : fullBodyText ? (
          <div className="text-sm text-foreground/85 leading-relaxed whitespace-pre-wrap max-w-2xl">
            {fullBodyText}
          </div>
        ) : !view.bodyHtml && !view.bodyText ? (
          <div className="text-sm text-muted-foreground italic py-4">
            This message has no preview text.{" "}
            <button
              onClick={async () => {
                setLoadingFullBody(true);
                try {
                  const fb = await fetchFullBody(view.id);
                  setFullBodyText(fb.body_text || "(empty message)");
                } catch {
                  setFullBodyText("(failed to load message)");
                } finally {
                  setLoadingFullBody(false);
                }
              }}
              className="text-primary hover:opacity-80 not-italic"
            >
              {loadingFullBody ? "Loading…" : "Load from provider"}
            </button>
          </div>
        ) : (
          <MessageContent html={view.bodyHtml} text={view.bodyText} />
        )}

        {/* "Load full message" button — appears when body was truncated at sync */}
        {view.bodyTruncated && !fullBodyText && (
          <div className="mt-2">
            <button
              onClick={async () => {
                setLoadingFullBody(true);
                try {
                  const fb = await fetchFullBody(view.id);
                  setFullBodyText(fb.body_text);
                } catch {
                  // keep truncated body on failure
                } finally {
                  setLoadingFullBody(false);
                }
              }}
              disabled={loadingFullBody}
              className="flex items-center gap-1.5 text-xs text-primary hover:opacity-80 transition-opacity disabled:opacity-40"
            >
              <AppIcon name="ExternalLink" size={12} />
              {loadingFullBody ? "Loading…" : "Load full message from provider"}
            </button>
            <p className="text-[10px] text-muted-foreground mt-1">
              This message was truncated to save storage. Click to fetch the complete body.
            </p>
          </div>
        )}

        {/* Attachments — same card UI the conversation view uses per message. */}
        {view.hasAttachments && (
          <AttachmentList
            attachments={view.attachments}
            className="mt-6 pt-4 border-t border-border"
          />
        )}
        </>
        )}

        {/* Reply / Forward composer */}
        {replyMode && (
          <div
            ref={composerRef}
            className="mt-8 border border-primary/30 rounded-lg overflow-hidden bg-secondary/30"
          >
            <div className="px-4 py-2 bg-secondary text-xs text-muted-foreground border-b border-border flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span>
                  Replying to{" "}
                  <span className="text-foreground">
                    {replyMode === "forward" ? "…" : replyTarget.from.name}
                  </span>
                </span>
                {/* Reply / Reply All mode toggle (hidden for forward) */}
                {replyMode !== "forward" && (
                  <div className="flex items-center bg-background rounded-md p-0.5 ml-2">
                    <button
                      onClick={() => switchReplyMode("reply-all")}
                      className={`px-1.5 py-0.5 rounded text-[10px] transition-colors ${
                        replyMode === "reply-all" ? "bg-primary text-primary-foreground" : "hover:text-foreground"
                      }`}
                      title="Reply to all"
                    >
                      <AppIcon name="ReplyAll" size={11} />
                    </button>
                    <button
                      onClick={() => switchReplyMode("reply")}
                      className={`px-1.5 py-0.5 rounded text-[10px] transition-colors ${
                        replyMode === "reply" ? "bg-primary text-primary-foreground" : "hover:text-foreground"
                      }`}
                      title="Reply to sender only"
                    >
                      <AppIcon name="Reply" size={11} />
                    </button>
                  </div>
                )}
              </div>
              <button
                className="text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => {
                  // The X keeps the draft, so the pending save runs at once
                  // (EM-G3c-2 item 11).
                  autosave.flush();
                  setReplyMode(null);
                }}
              >
                <AppIcon name="X" size={14} />
              </button>
            </div>
            {/* From — two or more mailboxes (EM-T8c, D-EM-20) */}
            {accounts.length > 1 && fromId && (
              <div className="px-4 py-1.5 border-b border-border">
                <FromRow
                  accounts={accounts}
                  value={fromId}
                  defaultValue={mailboxId ?? undefined}
                  onChange={(id) => void changeFrom(id)}
                  warning={fromWarning({
                    fromId,
                    accounts,
                    conversationAccountId: replyMode === "forward" ? null : mailboxId,
                    recipients: [...replyTo.split(","), ...replyCc.split(",")],
                    usualSender,
                    authErrors,
                  })}
                  authErrors={authErrors}
                  labelClassName="text-[10px] text-muted-foreground w-7 flex-shrink-0"
                />
              </div>
            )}
            {/* Recipients */}
            <div className="px-4 py-1.5 border-b border-border flex items-center gap-2">
              <span className="text-[10px] text-muted-foreground w-7 flex-shrink-0">To</span>
              <RecipientInput
                value={replyTo}
                onChange={(v) => { replyDirty.current = true; setReplyTo(v); }}
                accountId={fromId}
                ariaLabel="To recipients"
                placeholder="Recipients (comma-separated)…"
                className="w-full bg-transparent text-xs text-foreground placeholder:text-muted-foreground outline-none"
              />
              {/* Reveal Cc/Bcc on a sender-only reply where they're hidden. */}
              {!showReplyCc && (
                <Button variant="text" size="none" layout="" type="button" onClick={() => setShowReplyCc(true)} className="text-[10px] whitespace-nowrap px-1 flex-shrink-0">
                  Cc/Bcc
                </Button>
              )}
            </div>
            {showReplyCc && (
              <>
                <div className="px-4 py-1.5 border-b border-border flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground w-7 flex-shrink-0">Cc</span>
                  <RecipientInput
                    value={replyCc}
                    onChange={(v) => { replyDirty.current = true; setReplyCc(v); }}
                    accountId={fromId}
                    ariaLabel="Cc recipients"
                    placeholder="Cc…"
                    className="w-full bg-transparent text-xs text-foreground placeholder:text-muted-foreground outline-none"
                  />
                </div>
                <div className="px-4 py-1.5 border-b border-border flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground w-7 flex-shrink-0">Bcc</span>
                  <RecipientInput
                    value={replyBcc}
                    onChange={(v) => { replyDirty.current = true; setReplyBcc(v); }}
                    accountId={fromId}
                    ariaLabel="Bcc recipients"
                    placeholder="Bcc…"
                    className="w-full bg-transparent text-xs text-foreground placeholder:text-muted-foreground outline-none"
                  />
                </div>
              </>
            )}
            <textarea
              value={replyBody}
              onChange={(e) => { replyDirty.current = true; setReplyBody(e.target.value); }}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  void handleInlineSend();
                }
              }}
              placeholder={`Write your ${replyLabel.toLowerCase()}…`}
              rows={6}
              autoFocus
              className="w-full bg-transparent px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground outline-none resize-none"
            />
            {(replyAttachments.length > 0 || replyArtifacts.length > 0) && (
              <div className="px-4 pb-2 flex flex-wrap gap-1.5">
                {replyAttachments.map((a, i) => (
                  <span
                    key={`f-${i}`}
                    className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border border-border bg-secondary text-muted-foreground"
                  >
                    <AppIcon name="Paperclip" size={10} />
                    <span className="truncate max-w-[140px]" title={a.filename}>
                      {a.filename}
                    </span>
                    <button
                      onClick={() =>
                        setReplyAttachments((prev) => prev.filter((_, j) => j !== i))
                      }
                      className="hover:text-foreground"
                      title="Remove attachment"
                    >
                      <AppIcon name="X" size={10} />
                    </button>
                  </span>
                ))}
                {replyArtifacts.map((a, i) => (
                  <span
                    key={`a-${i}`}
                    className="inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border border-primary/40 bg-primary/5 text-primary"
                    title={a.path}
                  >
                    <AppIcon name="Paperclip" size={10} />
                    <span className="truncate max-w-[140px]">{a.name || a.path}</span>
                    <button
                      onClick={() =>
                        setReplyArtifacts((prev) => prev.filter((_, j) => j !== i))
                      }
                      className="hover:text-foreground"
                      title="Remove attachment"
                    >
                      <AppIcon name="X" size={10} />
                    </button>
                  </span>
                ))}
              </div>
            )}
            {/* The signature is IN the editable body (seeded on open) — no
                separate preview card; what you see is what the draft stores. */}
            {/* Quoted trailing email — collapsed, read-only (reattached on send) */}
            <ComposerQuote quote={replyQuote} />
            {/* AI draft/improve bar */}
            {aiOpen && (
              <DraftAssistant
                instruction={aiInstruction}
                onInstruction={setAiInstruction}
                busy={ai.busy}
                hasText={stripSignature(replyBody, sigText).trim().length > 0}
                hasDraft={ai.hasDraft}
                steps={ai.steps}
                thinking={ai.thinking}
                revisions={ai.revisions}
                activeRevision={ai.activeRevision}
                elapsedMs={ai.elapsedMs}
                onRun={runAiDraft}
                onRestore={(id) => ai.restore(id)}
                onClose={() => setAiOpen(false)}
              />
            )}
            {/* A failed send, a refused pick or a failed save (EM-G3c-2 items
                10, 13 and 14). It takes its own line: the footer holds six
                controls, and its status text truncates at 1440 px. */}
            {(sendErr || saveFailure) && (
              <p role="alert" className="px-4 py-1.5 text-[10px] text-destructive">
                {sendErr ?? saveFailure}
              </p>
            )}
            {/* Footer — on phones the labels compress to icons so the full
                action row (incl. Send) always fits inside the card. */}
            <div className="px-3 sm:px-4 py-2 bg-secondary/50 border-t border-border flex items-center justify-between gap-2">
              <span className="text-[10px] truncate min-w-0">
                {draftStatus === "saving" ? (
                  <span className="text-muted-foreground">Saving draft…</span>
                ) : draftStatus === "saved" ? (
                  <span className="text-muted-foreground">Draft saved · Ctrl+Enter to send</span>
                ) : (
                  <span className="text-muted-foreground">Ctrl+Enter to send</span>
                )}
              </span>
              <div className="flex gap-1 sm:gap-2 flex-shrink-0 items-center">
                <AiButton active={aiOpen} onClick={() => setAiOpen((v) => !v)} />
                <label
                  className="px-2 py-1 text-xs rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors cursor-pointer flex items-center"
                  title="Attach files"
                >
                  <AppIcon name="Paperclip" size={13} />
                  <input
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(e) => {
                      void addReplyFiles(e.target.files);
                      e.target.value = "";
                    }}
                  />
                </label>
                <ArtifactAttachPicker
                  exclude={replyArtifacts.map((a) => a.path)}
                  onPick={(ref) => {
                    replyDirty.current = true;
                    setReplyArtifacts((prev) =>
                      prev.some((a) => a.path === ref.path) ? prev : [...prev, ref]);
                  }}
                />
                <Button variant="ghost" size="none" radius="keep" layout="flex items-center" onClick={() => void popOutToComposer()} title="Open in the full composer (Bcc, attachments)" aria-label="Pop out to full composer" className="px-2 sm:px-3 py-1 text-xs rounded-md gap-1">
                  <AppIcon name="ExternalLink" size={13} />
                  <span className="hidden sm:inline">Pop out</span>
                </Button>
                <Button variant="ghost" size="none" radius="keep" layout="flex items-center" onClick={() => void discardReply()} title="Discard this draft" aria-label="Discard this draft" className="px-2 sm:px-3 py-1 text-xs rounded-md gap-1">
                  <AppIcon name="Trash2" size={13} />
                  <span className="hidden sm:inline">Discard</span>
                </Button>
                <Button size="none" radius="keep" layout="flex items-center" icon="Send" loading={sending} disabled={!replyTo.trim() || !replyBody.trim()} onClick={() => void handleInlineSend()} className="px-4 py-1 text-xs rounded-md gap-1.5">
                  Send
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>

      {showTimeline && email && (
        <MessageTimelineModal
          messageId={email.id}
          subject={email.subject}
          onClose={() => setShowTimeline(false)}
        />
      )}
    </div>
  );
}

function TBtn({
  icon: Icon,
  label,
  onClick,
  active,
}: {
  icon: React.ElementType;
  label: string;
  onClick: () => void;
  active?: boolean;
}) {
  return (
    <button
      title={label}
      onClick={onClick}
      className={`p-1.5 rounded transition-colors ${
        active
          ? "bg-primary/15 text-primary"
          : "text-muted-foreground hover:text-foreground hover:bg-secondary"
      }`}
    >
      <Icon size={13} />
    </button>
  );
}

function Divider() {
  return <div className="w-px h-4 bg-border" />;
}

