"use client";

/**
 * EmailAssistantChat — the email app's AI chat rail.
 *
 * This is a THIN wrapper around the shared <AgentChat> (the same component the
 * main chat app uses), pinned to the `email-assistant` agent.  It does NOT
 * reimplement streaming, tool rendering, recovery, persistence, or compaction —
 * all of that comes from the shared chat infrastructure, so improvements to the
 * chat app automatically flow into the email app.
 *
 * The wrapper's only jobs are:
 *   1. Manage the email-assistant session list (`useAgentSessions`, scoped to
 *      agentName="email-assistant" — so these conversations are the SAME
 *      objects the chat app sees — and to the signed-in member and org).
 *   2. Feed the agent the user's current email context (the chat scope, one
 *      mailbox or All inboxes, + the open email) so it can act on "this email"
 *      without the user repeating ids (EM-T8e-3, `lib/chatScope.ts`).
 *   3. Bridge the Assistant "Fix" flow (pendingChatPrompt → composer).
 */

import Icon from "@/components/Icon";
import { useState, useEffect, useCallback, useMemo } from "react";
import { useSession } from "next-auth/react";
import AgentChat from "@/components/AgentChat";
import { useAgentSessions } from "@/hooks/useChatSessions";
import { useActiveSessions } from "@/hooks/useActiveSessions";
import { useChatMemories } from "@/hooks/useChatMemories";
import { useEmailStore } from "../lib/emailStore";
import {
  buildEmailAssistantPersona,
  type PersonaAccountSettings,
} from "../lib/emailAssistantPersona";
import {
  chatMailboxOptions,
  chatScope,
  chatSettingsRead,
  rememberChatScope,
  type ChatScopeMemory,
  type ChatScopePick,
} from "../lib/chatScope";
import { getAssistantSettings } from "../lib/api";
import { useTierRouted } from "@/hooks/useTierRouted";
import { governedModelProps, readsChatModel } from "@/lib/tierRouting";

const AGENT = "email-assistant";

interface EmailAssistantChatProps {
  /** The scope of the page: `ALL_INBOXES` or a mailbox id (EM-T8e-3). The
   *  chat starts on it. In All inboxes the page passes `ALL_INBOXES`, never
   *  its hidden `selectedAccountId`. */
  pageScope?: string | null;
  selectedEmailId?: string | null;
  /** When set, renders a back button in the header — used when the chat is a
   *  full scene (like Assistant / Reply Zero) rather than a sidebar rail. */
  onClose?: () => void;
}

export function EmailAssistantChat({
  pageScope,
  selectedEmailId,
  onClose,
}: EmailAssistantChatProps) {
  const { data: nextAuthSession } = useSession();
  const userId: string = nextAuthSession?.user?.email ?? "dev@fracktal.in";

  const accounts = useEmailStore((s) => s.accounts);
  const emails = useEmailStore((s) => s.emails);
  const pendingChatPrompt = useEmailStore((s) => s.pendingChatPrompt);
  const setPendingChatPrompt = useEmailStore((s) => s.setPendingChatPrompt);

  const activeRunIds = useActiveSessions();

  const [showSessions, setShowSessions] = useState(false);
  const [pendingInput, setPendingInput] = useState<string | undefined>();

  // The scope of the CONVERSATION: one mailbox, or All inboxes (EM-T8e-3,
  // D-EM-23). It starts on the scope of the page, and the composer's picker
  // can point the assistant elsewhere without moving the page: a pick calls
  // neither selectAccount nor selectAll. Held as {against, scope} rather than
  // reset by an effect: the pick is valid only while the page scope it was
  // made against still stands, so moving the page drops it derivationally.
  // A scope on a removed mailbox falls back by pickInitialView (chatScope).
  const [scopePick, setScopePick] = useState<ChatScopePick | null>(null);
  const {
    allInboxes: chatAllInboxes,
    accountId: chatAccountId,
    pickerId,
  } = chatScope(accounts, pageScope ?? null, scopePick);
  const pickChatScope = useCallback(
    (scope: string) => setScopePick({ against: pageScope ?? null, scope }),
    [pageScope],
  );
  const mailboxOptions = useMemo(() => chatMailboxOptions(accounts), [accounts]);

  // The scope is a pure derivation, so a mailbox that leaves is gone from it.
  // The chat holds the scope of the render before, and when the mailbox of
  // that scope leaves, it shows one note that names it and the new scope
  // (EM-T8f-3, §11.6 case 17). rememberChatScope gives back the same object
  // for the same scope, so this update stops after one more render.
  const [scopeMemory, setScopeMemory] = useState<ChatScopeMemory | null>(null);
  const nextScopeMemory = rememberChatScope(scopeMemory, accounts, pickerId);
  if (nextScopeMemory !== scopeMemory) setScopeMemory(nextScopeMemory);
  const scopeNotice = nextScopeMemory.note
    ? {
        text: nextScopeMemory.note,
        onDismiss: () => setScopeMemory((m) => (m ? { ...m, note: null } : m)),
      }
    : null;

  // The CHAT mailbox's assistant settings. Two things ride on this, both
  // per-account: which chat model to run (the single source of truth is
  // Assistant → Settings → Models, not AgentChat's generic per-agent picker),
  // and the standing configuration (about / instructions / writing style) the
  // persona hands the agent — so switching mailboxes switches how the assistant
  // behaves, not just which account_id it passes to tools. Defaults to
  // tier-powerful so a send during the fetch window uses a sensible model.
  // All inboxes reads no settings: each mailbox has its own (D-EM-24), so the
  // chat runs on tier-powerful there.
  const [chatModel, setChatModel] = useState<string | undefined>("tier-powerful");
  // WS-45 S4 (D90): a covered email-assistant runs on the platform's tier,
  // so the chat neither reads nor passes `chat_model`. With the UI flag off
  // it is known and not covered, so the read and the props are as before.
  const tier = useTierRouted(AGENT);
  const readsModel = readsChatModel(tier);
  const [acctSettings, setAcctSettings] =
    useState<PersonaAccountSettings | null>(null);
  useEffect(() => {
    const read = chatSettingsRead(
      { allInboxes: chatAllInboxes, accountId: chatAccountId },
      getAssistantSettings,
    );
    if (!read) {
      setChatModel("tier-powerful");
      setAcctSettings(null);
      return;
    }
    // Clear the settings of the mailbox before, so that its standing orders
    // never stand under the name of the new one while the read is out, or
    // after the read fails (D-EM-18, D-EM-24; EM-T8e-3 review F1).
    setChatModel("tier-powerful");
    setAcctSettings(null);
    let cancelled = false;
    read
      .then((s) => {
        if (cancelled) return;
        // WS-45 S4: a covered email-assistant reads no `chat_model`.
        if (readsModel) setChatModel(s.chat_model || "tier-powerful");
        setAcctSettings({
          about: s.about,
          personal_instructions: s.personal_instructions,
          writing_style: s.writing_style,
          learned_writing_style: s.learned_writing_style,
        });
      })
      .catch(() => {
        // The default stays: tier-powerful, and no standing orders. The
        // persona still names the mailbox.
      });
    return () => {
      cancelled = true;
    };
  }, [chatAllInboxes, chatAccountId, readsModel]);

  // Inject Mem0 memories so the assistant has the SAME cross-conversation
  // continuity here as in the chat app (parity) — shared fetch + 30s poll via
  // useChatMemories; the agent persona only needs the memory text.
  const { memories: memoryObjs } = useChatMemories(userId);
  const memories = useMemo(
    () => memoryObjs.map((m) => m.memory).filter(Boolean),
    [memoryObjs],
  );

  // The list is the signed-in member's in this org, and a restored chat the
  // server refuses gives way to a new one (production bug, 2026-10-05).
  const {
    mine: emailSessions,
    activeId,
    activeSession,
    newSession: openNewSession,
    switchSession: openSession,
    removeSession,
    handleActivity,
    onSessionRefused,
    recoveredInput,
    consumeRecoveredInput,
    notice: recoveryNotice,
  } = useAgentSessions(AGENT);

  /* eslint-disable react-hooks/set-state-in-effect */
  // The Assistant's "Fix" flow hands a correction prompt through the store —
  // drop it into the composer (the user reviews & sends it).
  useEffect(() => {
    if (pendingChatPrompt) {
      setPendingInput(pendingChatPrompt);
      setPendingChatPrompt(null);
    }
  }, [pendingChatPrompt, setPendingChatPrompt]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const newSession = useCallback(() => {
    openNewSession();
    setShowSessions(false);
  }, [openNewSession]);

  const switchSession = useCallback((id: string) => {
    openSession(id);
    setShowSessions(false);
  }, [openSession]);

  // Compose the email context the agent operates with — the connected accounts,
  // the scope of the chat, and the currently-open email — via the SHARED builder
  // the chat app also uses, so running the assistant here vs in the chat app is
  // the same experience (the open email is the only email-app-specific extra).
  // The open email names its mailbox in both scopes (EM-T8e-3).
  const emailContextStr = useMemo(
    () =>
      buildEmailAssistantPersona({
        accounts,
        selectedAccountId: chatAccountId,
        allInboxes: chatAllInboxes,
        openEmail: emails.find((e) => e.id === selectedEmailId) ?? null,
        settings: chatAllInboxes ? null : acctSettings,
      }),
    [accounts, emails, chatAccountId, chatAllInboxes, selectedEmailId, acctSettings],
  );


  return (
    <div className="flex flex-col h-full bg-sidebar text-sidebar-foreground overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-9 border-b border-sidebar-border flex-shrink-0">
        <div className="flex items-center gap-2">
          {onClose && (
            <button
              onClick={onClose}
              title="Back to inbox"
              aria-label="Back to inbox"
              className="p-1 -ml-1 rounded text-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors"
            >
              <Icon name="ArrowLeft" size={14} />
            </button>
          )}
          <div className="w-5 h-5 rounded-full bg-primary/20 text-primary flex items-center justify-center">
            <Icon name="Sparkles" size={11} />
          </div>
          <span className="text-xs font-semibold text-sidebar-foreground">
            AI Assistant
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => setShowSessions((v) => !v)}
            title="Chat history"
            className={`p-1 rounded transition-colors ${
              showSessions
                ? "text-primary bg-primary/10"
                : "text-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent"
            }`}
          >
            <Icon name="MessagesSquare" size={14} />
          </button>
          <button
            onClick={newSession}
            title="New chat"
            className="p-1 rounded text-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors"
          >
            <Icon name="Plus" size={15} />
          </button>
        </div>
      </div>

      {/* Sessions list */}
      {showSessions && (
        <div className="border-b border-sidebar-border bg-secondary/30 max-h-56 overflow-y-auto scrollbar-hide flex-shrink-0">
          <div className="flex items-center justify-between px-3 py-1.5">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Conversations
            </span>
            <button
              onClick={() => setShowSessions(false)}
              className="text-muted-foreground hover:text-foreground"
            >
              <Icon name="X" size={12} />
            </button>
          </div>
          {emailSessions.length === 0 ? (
            <div className="px-3 py-2 text-[11px] text-muted-foreground">
              No conversations yet.
            </div>
          ) : (
            emailSessions.map((s) => (
              <div
                key={s.id}
                className={`group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors ${
                  s.id === activeId ? "bg-primary/10" : "hover:bg-secondary/60"
                }`}
                onClick={() => switchSession(s.id)}
              >
                {activeRunIds.has(s.id) ? (
                  <span
                    className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse flex-shrink-0"
                    title="Active — agent is working"
                  />
                ) : (
                  <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/30 flex-shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <div className="text-[11px] text-foreground truncate">
                    {s.title || "New conversation"}
                  </div>
                  {s.lastPreview && (
                    <div className="text-[10px] text-muted-foreground truncate">
                      {s.lastPreview}
                    </div>
                  )}
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    removeSession(s.id);
                  }}
                  title="Delete conversation"
                  className="reveal-on-hover text-muted-foreground hover:text-destructive flex-shrink-0"
                >
                  <Icon name="Trash2" size={12} />
                </button>
              </div>
            ))
          )}
        </div>
      )}

      {/* Shared chat — the same AgentChat the main chat app renders */}
      <div className="flex-1 min-h-0">
        {activeSession && (
          <AgentChat
            key={activeSession.id}
            agentName={AGENT}
            sessionId={activeSession.id}
            compact
            {...governedModelProps(tier.covered, chatModel)}
            persona={emailContextStr}
            emailContext={{ accountId: chatAccountId, emailId: selectedEmailId }}
            mailboxes={mailboxOptions}
            activeMailboxId={pickerId}
            onMailboxChange={pickChatScope}
            notice={recoveryNotice ?? scopeNotice}
            memories={memories}
            memoryUserId={userId}
            expectedMessageCount={activeSession.messageCount}
            onActivity={handleActivity}
            onSessionRefused={onSessionRefused}
            pendingInput={pendingInput ?? recoveredInput}
            onPendingInputConsumed={() => {
              setPendingInput(undefined);
              consumeRecoveredInput();
            }}
          />
        )}
      </div>
    </div>
  );
}
