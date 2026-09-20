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
 *   1. Manage the email-assistant session list (via the shared @/lib/sessions
 *      store, scoped to agentName="email-assistant" — so these conversations are
 *      the SAME objects the chat app sees).
 *   2. Feed the agent the user's current email context (selected account + open
 *      email) so it can act on "this email" without the user repeating ids.
 *   3. Bridge the Assistant "Fix" flow (pendingChatPrompt → composer).
 */

import Icon from "@/components/Icon";
import { useState, useEffect, useCallback, useMemo } from "react";
import { useSession } from "next-auth/react";
import AgentChat from "@/components/AgentChat";
import {
  getSessions, createSession, upsertSession, deleteSession,
  enrichSession, fetchAndMergeSessionsFromDb, type ChatSession,
} from "@/lib/sessions";
import { useActiveSessions } from "@/hooks/useActiveSessions";
import { useChatMemories } from "@/hooks/useChatMemories";
import { useEmailStore } from "../lib/emailStore";
import {
  buildEmailAssistantPersona,
  type PersonaAccountSettings,
} from "../lib/emailAssistantPersona";
import { getAssistantSettings } from "../lib/api";

const AGENT = "email-assistant";

interface EmailAssistantChatProps {
  selectedAccountId?: string | null;
  selectedEmailId?: string | null;
  /** When set, renders a back button in the header — used when the chat is a
   *  full scene (like Assistant / Reply Zero) rather than a sidebar rail. */
  onClose?: () => void;
}

export function EmailAssistantChat({
  selectedAccountId,
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

  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [showSessions, setShowSessions] = useState(false);
  const [pendingInput, setPendingInput] = useState<string | undefined>();

  // Which mailbox the CONVERSATION is about. Defaults to whatever the inbox is
  // showing, but the composer's mailbox picker can point the assistant at a
  // different one without making the user leave the thread (or change what
  // they're reading). Cleared when the inbox selection moves, so the two only
  // diverge while the user deliberately holds them apart.
  // Held as {against, id} rather than reset by an effect: the override is valid
  // only while the inbox selection it was made against still stands, so moving
  // the inbox drops it derivationally — no cascading render, no stale window
  // where the two disagree.
  const [chatAccountOverride, setChatAccountOverride] = useState<
    { against: string | null; id: string } | null
  >(null);
  const chatAccountId =
    (chatAccountOverride?.against === (selectedAccountId ?? null)
      ? chatAccountOverride.id
      : null) ??
    selectedAccountId ??
    null;
  const pickChatAccount = useCallback(
    (id: string) =>
      setChatAccountOverride({ against: selectedAccountId ?? null, id }),
    [selectedAccountId],
  );
  const mailboxOptions = useMemo(
    () =>
      accounts.map((a) => ({
        id: a.id,
        label: a.emailAddress || a.id,
      })),
    [accounts],
  );

  // The CHAT mailbox's assistant settings. Two things ride on this, both
  // per-account: which chat model to run (the single source of truth is
  // Assistant → Settings → Models, not AgentChat's generic per-agent picker),
  // and the standing configuration (about / instructions / writing style) the
  // persona hands the agent — so switching mailboxes switches how the assistant
  // behaves, not just which account_id it passes to tools. Defaults to
  // tier-powerful so a send during the fetch window uses a sensible model.
  const [chatModel, setChatModel] = useState<string | undefined>("tier-powerful");
  const [acctSettings, setAcctSettings] =
    useState<PersonaAccountSettings | null>(null);
  useEffect(() => {
    if (!chatAccountId) {
      setChatModel("tier-powerful");
      setAcctSettings(null);
      return;
    }
    let cancelled = false;
    getAssistantSettings(chatAccountId)
      .then((s) => {
        if (cancelled) return;
        setChatModel(s.chat_model || "tier-powerful");
        setAcctSettings({
          about: s.about,
          personal_instructions: s.personal_instructions,
          writing_style: s.writing_style,
          learned_writing_style: s.learned_writing_style,
        });
      })
      .catch(() => {
        // Keep the tier-powerful default if the lookup fails; the persona
        // degrades to account-awareness without the standing orders.
      });
    return () => {
      cancelled = true;
    };
  }, [chatAccountId]);

  // Inject Mem0 memories so the assistant has the SAME cross-conversation
  // continuity here as in the chat app (parity) — shared fetch + 30s poll via
  // useChatMemories; the agent persona only needs the memory text.
  const { memories: memoryObjs } = useChatMemories(userId);
  const memories = useMemo(
    () => memoryObjs.map((m) => m.memory).filter(Boolean),
    [memoryObjs],
  );

  const emailSessions = useMemo(
    () => sessions.filter((s) => s.agentName === AGENT),
    [sessions],
  );

  // Restore the most recent email-assistant session (or start one) on mount.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const existing = getSessions().filter((s) => s.agentName === AGENT);
    if (existing.length > 0) {
      setSessions(getSessions());
      setActiveId(existing[0].id);
    } else {
      const s = createSession(AGENT);
      upsertSession(s);
      setSessions(getSessions());
      setActiveId(s.id);
    }
  }, []);

  // Merge email-assistant sessions that live only in Postgres (cache clear,
  // other device, or created from the main chat app).
  useEffect(() => {
    let cancelled = false;
    fetchAndMergeSessionsFromDb()
      .then((merged) => { if (!cancelled) setSessions(merged); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

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
    const s = createSession(AGENT);
    upsertSession(s);
    setSessions(getSessions());
    setActiveId(s.id);
    setShowSessions(false);
  }, []);

  const switchSession = useCallback((id: string) => {
    setActiveId(id);
    setShowSessions(false);
  }, []);

  const removeSession = useCallback(
    (id: string) => {
      deleteSession(id);
      const remaining = getSessions().filter((s) => s.agentName === AGENT);
      setSessions(getSessions());
      if (id === activeId) {
        if (remaining.length > 0) {
          setActiveId(remaining[0].id);
        } else {
          const s = createSession(AGENT);
          upsertSession(s);
          setSessions(getSessions());
          setActiveId(s.id);
        }
      }
    },
    [activeId],
  );

  const handleActivity = useCallback(
    (info: { firstUserMessage?: string; lastPreview?: string; messageCount: number }) => {
      enrichSession(activeId, info);
      setSessions(getSessions());
    },
    [activeId],
  );

  // Compose the email context the agent operates with — the connected accounts,
  // the selected account, and the currently-open email — via the SHARED builder
  // the chat app also uses, so running the assistant here vs in the chat app is
  // the same experience (the open email is the only email-app-specific extra).
  const emailContextStr = useMemo(
    () =>
      buildEmailAssistantPersona({
        accounts,
        selectedAccountId: chatAccountId,
        openEmail: emails.find((e) => e.id === selectedEmailId) ?? null,
        settings: acctSettings,
      }),
    [accounts, emails, chatAccountId, selectedEmailId, acctSettings],
  );

  const activeSession = emailSessions.find((s) => s.id === activeId);

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
            model={chatModel}
            lockModel
            persona={emailContextStr}
            emailContext={{ accountId: chatAccountId, emailId: selectedEmailId }}
            mailboxes={mailboxOptions}
            activeMailboxId={chatAccountId}
            onMailboxChange={pickChatAccount}
            memories={memories}
            memoryUserId={userId}
            expectedMessageCount={activeSession.messageCount}
            onActivity={handleActivity}
            pendingInput={pendingInput}
            onPendingInputConsumed={() => setPendingInput(undefined)}
          />
        )}
      </div>
    </div>
  );
}
