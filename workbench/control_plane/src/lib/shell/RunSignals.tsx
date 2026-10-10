"use client";

/**
 * The signals of an assistant run outside its open chat (WS-51 S5,
 * `project-docs/specs/chat_run_continuity.md` §4 S5).
 *
 * **The shell owns them** (rule 10 of `AGENTS.md`). `AppShell` mounts
 * `RunSignalsHost` once in every layout, and `AgentPill` once on the phone.
 * The rules are pure functions in `lib/runSignals.ts`. This file only feeds
 * them and draws.
 *
 *   - `RunSignalsHost` draws nothing. On each list from the S1 store it marks
 *     the chats of the runs that ended as unread, and it raises one toast per
 *     finished run through the shell's one toast viewport (`useToast`). It
 *     sets the title and the favicon of a hidden tab, and it gives them back
 *     when the tab shows.
 *   - `AgentPill` is a slim bar above the phone's bottom nav while a run goes
 *     on. Only the pill asks the store for each run's step, and only while it
 *     shows in a visible tab.
 *
 * Nothing here polls, and nothing here reads a list that the badges do not.
 *
 * Fence (R7): `src/lib/runSignals.test.ts`.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect } from "react";

import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import { useToast } from "@/components/ui/Toast";
import {
  localTitle,
  useActivityRows,
  useLocalActive,
  useOpenChats,
  useRunActivity,
  useServerRuns,
} from "@/hooks/useActiveSessions";
import { shouldPollWorkspace } from "@/lib/access";
import { getSessionAgent } from "@/lib/chatStore";
import { liveRunsDegradedAt, liveRunsGeneration, wantLiveSteps } from "@/lib/liveRuns";
import { chatLink } from "@/lib/runActivity";
import {
  applyTabSignal,
  clearUnread,
  getOpenChats,
  getUnread,
  observeRuns,
  onTabVisible,
  pillModel,
  subscribeUnread,
  toastPlan,
  type FinishedRun,
  type TabDocument,
} from "@/lib/runSignals";

import { openActivity, useHeldHrefs } from "./ActivityControl";

function tabVisible(): boolean {
  return typeof document === "undefined" || document.visibilityState !== "hidden";
}

/** The document, as the tab signal sees it. */
function tabDocument(): TabDocument {
  return document as unknown as TabDocument;
}

/**
 * Unread marks, the finished toast, and the hidden tab's title and favicon.
 * One per app, in `AppShell`. It renders nothing.
 */
export function RunSignalsHost() {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  const serverRuns = useServerRuns(workspace);
  const localActive = useLocalActive();
  const { needsTotal, unreadTotal } = useRunActivity(null, workspace);
  const heldHrefs = useHeldHrefs();
  const toast = useToast();
  const router = useRouter();

  // One toast per finished run, up to two. "Open" takes the open-chat job's
  // link, the same link as a row of the activity panel. Three or more in one
  // batch show ONE summary, whose "Open" shows the activity panel.
  const showFinished = useCallback(
    (runs: readonly FinishedRun[], held: ReadonlySet<string>) => {
      const plan = toastPlan(runs);
      if (plan.kind === "summary") {
        const t = plan.toast;
        toast.show({
          key: t.key,
          variant: "success",
          title: t.title,
          timeout: t.timeout,
          action: { label: t.actionLabel, onClick: () => openActivity() },
        });
        return;
      }
      for (const { run, toast: t } of plan.toasts) {
        toast.show({
          key: t.key,
          variant: "success",
          title: t.title,
          description: t.description,
          timeout: t.timeout,
          action: { label: t.actionLabel, onClick: () => router.push(chatLink(run.agent, run.threadId, held)) },
        });
      }
    },
    [toast, router],
  );

  // Each new list: compare it with the last one.
  useEffect(() => {
    if (!workspace) return;
    const toasts = observeRuns({
      generation: liveRunsGeneration(),
      server: serverRuns,
      localIds: localActive,
      localAgent: getSessionAgent,
      localTitle,
      visible: tabVisible(),
      now: Date.now(),
      degradedAt: liveRunsDegradedAt(),
    });
    if (toasts.length) showFinished(toasts, heldHrefs);
  }, [workspace, serverRuns, localActive, heldHrefs, showFinished]);

  // The tab shows again: the open chats are read, and a run that ended while
  // it was hidden gets its toast now.
  useEffect(() => {
    if (!workspace || typeof document === "undefined") return;
    const onVisibility = () => {
      if (tabVisible()) {
        const toasts = onTabVisible();
        if (toasts.length) showFinished(toasts, heldHrefs);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [workspace, heldHrefs, showFinished]);

  // Another tab marked a chat unread that this tab shows now: it is read.
  useEffect(() => {
    if (!workspace) return;
    return subscribeUnread(() => {
      if (!tabVisible()) return;
      const unread = getUnread();
      for (const id of getOpenChats()) if (id in unread) clearUnread(id);
    });
  }, [workspace]);

  // The hidden tab's title and favicon. Restored when the tab shows, and when
  // the host goes.
  useEffect(() => {
    if (typeof document === "undefined") return;
    const apply = () => applyTabSignal(tabDocument(), workspace ? needsTotal : 0, workspace ? unreadTotal : 0);
    apply();
    document.addEventListener("visibilitychange", apply);
    return () => document.removeEventListener("visibilitychange", apply);
  }, [workspace, needsTotal, unreadTotal]);
  useEffect(
    () => () => {
      if (typeof document !== "undefined") applyTabSignal(tabDocument(), 0, 0);
    },
    [],
  );

  return null;
}

/** The attribute that lifts the toasts and the page above the pill (globals.css). */
export const PILL_ATTR = "data-agent-pill";

/**
 * The phone's agent pill: the newest live run that is not an open chat, its
 * step, a thin progress line, and "+N" for the others. A tap opens its chat.
 * Phone only: `AppShell` mounts it in the mobile layout.
 */
export function AgentPill() {
  const { access, loading } = useAccess();
  const workspace = shouldPollWorkspace(access, loading);
  const rows = useActivityRows(workspace);
  const open = useOpenChats();
  const heldHrefs = useHeldHrefs();
  const model = workspace ? pillModel(rows, open) : null;
  const shown = model !== null;

  // Only the pill asks for steps, and only while it shows in a visible tab.
  // A hidden tab keeps the cheap 30 s poll.
  useEffect(() => {
    if (!shown || typeof document === "undefined") return;
    let release: (() => void) | null = null;
    const sync = () => {
      if (tabVisible() && !release) release = wantLiveSteps();
      else if (!tabVisible() && release) {
        release();
        release = null;
      }
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      release?.();
    };
  }, [shown]);

  // Lift the toasts and the page's last row above the pill while it shows.
  useEffect(() => {
    if (!shown || typeof document === "undefined") return;
    document.documentElement.setAttribute(PILL_ATTR, "");
    return () => document.documentElement.removeAttribute(PILL_ATTR);
  }, [shown]);

  if (!model) return null;
  return <AgentPillView model={model} href={chatLink(model.row.agentName, model.row.threadId, heldHrefs)} />;
}

/**
 * The pill's look. Pure, so the tests read its markup. Tokens only, `z-50`
 * like the bottom nav it sits on, and the safe-area clearance of
 * `.agent-pill-bottom`. The progress line moves only when the member allows
 * motion.
 */
export function AgentPillView({ model, href }: { model: NonNullable<ReturnType<typeof pillModel>>; href: string }) {
  const needs = model.row.state === "needs_input";
  const label = `${model.text}${model.more > 0 ? `, and ${model.more} more` : ""}. Open the chat`;
  return (
    <div data-agent-pill-host className="agent-pill-bottom fixed inset-x-3 z-50">
      <Link
        href={href}
        data-agent-pill={needs ? "needs_input" : "running"}
        aria-label={label}
        className="relative flex h-10 items-center gap-2 overflow-hidden rounded-full border border-border bg-card/95 px-3 text-xs text-foreground shadow-lg backdrop-blur tech-transition hover:bg-secondary"
      >
        <span
          aria-hidden
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${needs ? "bg-warning" : "bg-success motion-safe:animate-pulse"}`}
        />
        <Icon name="Bot" size={14} className="shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate font-medium">{model.text}</span>
        {model.more > 0 ? (
          <span data-agent-pill-more className="shrink-0 text-[11px] font-medium text-muted-foreground">
            +{model.more}
          </span>
        ) : null}
        <Icon name="ChevronRight" size={14} className="shrink-0 text-muted-foreground" />
        {needs ? null : (
          // `bg-primary` follows the accent on purpose: this line is progress, not a status.
          <span aria-hidden className="agent-pill-track absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-primary/15">
            <span className="agent-pill-progress block h-full w-1/3 rounded-full bg-primary" />
          </span>
        )}
      </Link>
    </div>
  );
}
