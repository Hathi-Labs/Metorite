"use client";

/**
 * The chat session list of one agent, for one signed-in member in one org.
 *
 * Production bug, 2026-10-05. The Projects rail, the Tasks rail and the email
 * assistant each carried the same restore code, over a list the whole browser
 * shared. A second member on one browser reopened the first member's chat from
 * another org, and every send failed. This hook is that code ONCE, with the two
 * fixes:
 *
 * 1. The list is keyed by member and org (`useChatScope`). Nothing restores
 *    until the identity is known, so a stored id never crosses to another
 *    member.
 * 2. A RESTORED id the server refuses (on load, or on send) is dropped, and a
 *    new chat opens with no error card (`lib/railSessions.ts`). A chat the
 *    member opened on purpose keeps its error.
 *
 * `/chat` picks among every agent and has a picker, so it keeps its own list
 * code and uses `useChatScope` and `useRestoredSessionGuard` from here.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSession } from "next-auth/react";
import { useAccess } from "@/components/AccessProvider";
import {
  bindChatScope,
  clearSignedOutAccount,
  confirmSignedOut,
  deleteSession,
  enrichSession,
  fetchAndMergeSessionsFromDb,
  getSessions,
  isSignedOut,
  probeSession,
  scopeFromAccess,
  tidyChatStorage,
  type ChatSession,
} from "@/lib/sessions";
import {
  NO_PICK,
  carriedText,
  isRestored,
  openDeliberately,
  recoverRefused,
  recoveredNotice,
  refusalHandler,
  restoreOrStart,
  restoreOrStartAfterMerge,
  startFresh,
  type RailPick,
} from "@/lib/railSessions";
import type { SessionRefusedHandler } from "@/lib/chatTurnFailure";

/**
 * The signed-in member's chat scope, from `useAccess()` (the one identity
 * source of the client), or null until it resolves (`scopeFromAccess` has
 * the rules). It binds the chat namespace during render, so a child's effect
 * never reads it unbound. The storage work (`tidyChatStorage`) runs in an
 * effect, and it deletes nothing of any namespace: a switch is not a
 * sign-out.
 */
export function useChatScope(): string | null {
  const { access, loading, stale } = useAccess();
  const scope = scopeFromAccess({
    loading,
    stale,
    email: access.email,
    organizationId: access.organization?.id,
  });
  bindChatScope(scope);
  useEffect(() => {
    tidyChatStorage(scope);
  }, [scope]);
  return scope;
}

/**
 * Probe a restored id once, and recover from it when the server refuses it.
 * Returns the handler for `AgentChat`'s `onSessionRefused`, or undefined when
 * the open chat was opened on purpose.
 */
/**
 * Clear the namespace of the account that signed out, however it signed out
 * (`isSignedOut` has the rule). `AppShell` calls this once, on every page,
 * the `bindIdentity` pattern of `lib/dataCache.ts`: the clear is bound to the
 * identity, never to one sign-out button.
 */
export function useChatSignOutClear(): void {
  const { status } = useSession();
  const { access, loading, stale } = useAccess();
  const signedOut = isSignedOut({
    sessionStatus: status,
    accessLoading: loading,
    stale,
    email: access.email,
  });
  useEffect(() => {
    if (!signedOut) return;
    // Confirm with NextAuth before deleting anything (round 3): a failed
    // session fetch during a deploy also reads as "unauthenticated".
    let cancelled = false;
    void confirmSignedOut().then((confirmed) => {
      if (confirmed && !cancelled) clearSignedOutAccount();
    });
    return () => { cancelled = true; };
  }, [signedOut]);
}

export function useRestoredSessionGuard(
  activeId: string,
  restoredId: string | null,
  recover: (refusedId: string, pendingText?: string) => boolean,
): SessionRefusedHandler | undefined {
  const restored = isRestored({ activeId, restoredId });
  useEffect(() => {
    if (!restored) return;
    let cancelled = false;
    void probeSession(activeId).then((r) => {
      if (!cancelled && r === "refused") recover(activeId);
    });
    return () => { cancelled = true; };
  }, [restored, activeId, recover]);
  return useMemo(
    () => refusalHandler({ activeId, restoredId }, recover),
    [activeId, restoredId, recover],
  );
}

export interface AgentSessions {
  sessions: ChatSession[];
  /** This agent's sessions, newest first. */
  mine: ChatSession[];
  activeId: string;
  activeSession: ChatSession | undefined;
  newSession: () => void;
  switchSession: (id: string) => void;
  removeSession: (id: string) => void;
  handleActivity: (info: {
    firstUserMessage?: string;
    lastPreview?: string;
    messageCount: number;
  }) => void;
  /** For `AgentChat`'s `onSessionRefused`. Undefined for a chat opened on purpose. */
  onSessionRefused: SessionRefusedHandler | undefined;
  /** The refused message, for the composer of the new chat. */
  recoveredInput: string | undefined;
  consumeRecoveredInput: () => void;
  /** For `AgentChat`'s `notice`, after a refused chat gave way to a new one. */
  notice: { text: string; onDismiss: () => void } | null;
}

export function useAgentSessions(agent: string): AgentSessions {
  const scope = useChatScope();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [pick, setPick] = useState<RailPick>(NO_PICK);
  const pickRef = useRef<RailPick>(NO_PICK);
  const [recoveredInput, setRecoveredInput] = useState<string | undefined>();
  const [noticeText, setNoticeText] = useState<string | null>(null);

  const choose = useCallback((next: RailPick) => {
    pickRef.current = next;
    setPick(next);
  }, []);

  // Restore this member's newest session of the agent, or start one. It runs
  // again when the member or the org changes, so a chat never carries over.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setNoticeText(null);
    setRecoveredInput(undefined);
    if (!scope) {
      setSessions([]);
      choose(NO_PICK);
      return;
    }
    // An empty local list waits (bounded) for the server's list first, so a
    // first visit does not start an empty chat row while the server holds the
    // member's chats.
    let cancelled = false;
    void restoreOrStartAfterMerge(agent).then((next) => {
      if (cancelled) return;
      choose(next);
      setSessions(getSessions());
    });
    return () => { cancelled = true; };
  }, [scope, agent, choose]);

  // Merge the sessions that live only in Postgres (cache clear, other device,
  // or created from the main chat app).
  useEffect(() => {
    if (!scope) return;
    let cancelled = false;
    fetchAndMergeSessionsFromDb()
      .then((merged) => { if (!cancelled) setSessions(merged); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [scope]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const recover = useCallback(
    (refusedId: string, pendingText?: string): boolean => {
      const next = recoverRefused(pickRef.current, refusedId, agent);
      if (!next) return false;
      // The refused message and the refused chat's queue go to the composer.
      const carried = carriedText(refusedId, pendingText);
      choose(next);
      setSessions(getSessions());
      setRecoveredInput(carried);
      // On load and on send alike: a chat that disappears is explained.
      setNoticeText(recoveredNotice(carried));
      return true;
    },
    [agent, choose],
  );

  const onSessionRefused = useRestoredSessionGuard(pick.activeId, pick.restoredId, recover);

  const newSession = useCallback(() => {
    choose(startFresh(agent));
    setSessions(getSessions());
    setNoticeText(null);
  }, [agent, choose]);

  const switchSession = useCallback((id: string) => {
    choose(openDeliberately(id));
    setNoticeText(null);
  }, [choose]);

  const removeSession = useCallback(
    (id: string) => {
      deleteSession(id);
      if (id === pickRef.current.activeId) choose(restoreOrStart(agent));
      setSessions(getSessions());
    },
    [agent, choose],
  );

  const activeId = pick.activeId;
  const handleActivity = useCallback(
    (info: { firstUserMessage?: string; lastPreview?: string; messageCount: number }) => {
      enrichSession(activeId, info);
      setSessions(getSessions());
    },
    [activeId],
  );

  const mine = useMemo(() => sessions.filter((s) => s.agentName === agent), [sessions, agent]);
  const activeSession = mine.find((s) => s.id === activeId);
  const consumeRecoveredInput = useCallback(() => setRecoveredInput(undefined), []);
  const notice = useMemo(
    () => (noticeText ? { text: noticeText, onDismiss: () => setNoticeText(null) } : null),
    [noticeText],
  );

  return {
    sessions,
    mine,
    activeId,
    activeSession,
    newSession,
    switchSession,
    removeSession,
    handleActivity,
    onSessionRefused,
    recoveredInput,
    consumeRecoveredInput,
    notice,
  };
}
