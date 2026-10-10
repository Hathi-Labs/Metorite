"use client";

/**
 * The shell's ONE bell: "Needs you", from every app (`navigation_shell.md`
 * §7.2, NS-6 slice 6a). Behind `NEXT_PUBLIC_SHELL_DOCK`, default OFF
 * (`dockFlag.ts`).
 *
 * **The shell owns it** (rule 10 of `AGENTS.md`). The owner placed it on
 * 2026-10-10: the bell and the assistant toggle sit at the right end of the
 * shell bar, because "they belong to the whole product, not to an app". It
 * is ONE control, `ShellBell`, drawn where the activity control is drawn:
 *
 *   - the shell bar's right end, before the activity control (`ShellBar.tsx`),
 *     when `NEXT_PUBLIC_SHELL_BAR` is on;
 *   - the sidebar's head (`Sidebar.tsx`), when that flag is off;
 *   - the phone's Menu drawer (`AppShell.tsx`).
 *
 * `ShellBellHost` mounts the one panel. `AppShell` renders it while this
 * flag is on.
 *
 * **One feed, one list.** The bell reads `GET /api/shell/needs` through
 * `useNeedsYou`, with My Day's cache key, so the two stay in step. The panel
 * draws `NeedsList`, the same list My Day's "Needs you" card draws, so the
 * groups, the rows and the acts are the same code. A Done here is the My
 * Tasks store's gesture, with its subtask question and its Undo.
 *
 * **The count** is the number of rows that need the member: the feed's
 * `total`, every row the sources gave before the feed's cut to 30, less the
 * rows an act just took off. It is not a sum of unread mail. Each source gives
 * 15 rows at most, so it is a floor. The badge prints "99+" past 99, and
 * nothing at 0. When the feed was cut, the panel says "Showing 30 of 45".
 *
 * **A Done closes the panel.** Its Undo is THE toast, and the toast's
 * viewport sits under a dialog's scrim (`Toast.tsx`, and `DESIGN_SYSTEM.md`
 * §4a forbids a higher layer). The dialog also hides the toast from a screen
 * reader. So the panel gets out of the way, and the Undo is in reach for
 * every member. A Mark read has no Undo, so the panel stays.
 *
 * ⚠️ It is narrower than the Projects `NotificationBell` it replaces with the
 * flag on: unread notifications only, 15 for each source, no "Mark all
 * read", and no separate mentions count. `navigation_shell.md` §7.2 says so.
 *
 * Fence (R7): `shellBell.test.ts` and `e2e/shell-bell.spec.ts`.
 */

import Link from "next/link";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { UndoToastFallback } from "@/app/tasks/components/UndoToast";
import { useAccess } from "@/components/AccessProvider";
import Icon from "@/components/Icon";
import NavBadge from "@/components/NavBadge";
import Button from "@/components/ui/Button";
import Modal, { type ModalPlacement } from "@/components/ui/Modal";
import { onClear } from "@/lib/dataCache";

import { cardsFor, seesApprovals } from "./myDay";
import { NeedsList, type NeedsYou, useNeedsYou } from "./NeedsYouCard";
import { homePane } from "./shellNav";

// ── Whether the panel is up ──────────────────────────────────────────────────
//
// One small store, as `ActivityControl` has: any bell in any layout opens the
// one panel, and every bell can say whether it is open. Memory only.

let _open = false;
const _openListeners = new Set<() => void>();

function _setOpen(next: boolean): void {
  if (_open === next) return;
  _open = next;
  _openListeners.forEach((l) => l());
}

/** Open the bell's panel. */
export function openBell(): void {
  _setOpen(true);
}

/** Close the bell's panel. */
export function closeBell(): void {
  _setOpen(false);
}

function subscribeOpen(listener: () => void): () => void {
  _openListeners.add(listener);
  return () => {
    _openListeners.delete(listener);
  };
}

/** True while the panel is up. */
export function useBellOpen(): boolean {
  return useSyncExternalStore(subscribeOpen, () => _open, () => false);
}

// A new member on this browser starts with the panel closed.
onClear(() => _setOpen(false));

// ── The words ────────────────────────────────────────────────────────────────

/** The highest count the badge prints. Past it, the badge says "99+". */
export const BELL_MAX = 99;

/** The badge's text: `null` for nothing to count, "99+" past the cap. */
export function bellBadge(count: number | undefined): string | null {
  if (count === undefined || !Number.isFinite(count) || count <= 0) return null;
  return count > BELL_MAX ? `${BELL_MAX}+` : String(Math.floor(count));
}

/**
 * The bell's spoken name: what it is, then how many rows need the member.
 * It says the real number, also past the badge's cap.
 */
export function bellLabel(count: number | undefined): string {
  if (count === undefined || !Number.isFinite(count)) return "Needs you";
  const n = Math.max(0, Math.floor(count));
  if (n === 0) return "Needs you, no items";
  return `Needs you, ${n} ${n === 1 ? "item" : "items"}`;
}

/**
 * Whether this member can have a feed at all, and whether it holds the
 * approvals group. Both are My Day's own rules (`cardsFor`, `seesApprovals`),
 * so the bell and the card show the same rows.
 */
function useBellAllowed(): { allowed: boolean; approvals: boolean } {
  const { access, loading } = useAccess();
  if (loading) return { allowed: false, approvals: false };
  return {
    allowed: cardsFor(access.features, access.is_admin).needs,
    approvals: seesApprovals(access.features, access.is_admin),
  };
}

// ── The control ──────────────────────────────────────────────────────────────

/**
 * The bell's look: the Bell glyph with the count badge. Pure, so the tests
 * read its markup. `ShellBell` feeds it.
 */
export function BellButton({
  count,
  open,
  onOpen,
  className = "",
}: {
  /** The rows that need the member, or `undefined` before the feed answers. */
  count: number | undefined;
  open: boolean;
  onOpen: () => void;
  /** Layout only. */
  className?: string;
}) {
  const text = bellBadge(count);
  const label = bellLabel(count);
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      icon="Bell"
      data-shell-bell={text ? "count" : "quiet"}
      aria-label={label}
      title={label}
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={onOpen}
      className={`relative ${className}`}
    >
      {text ? (
        <NavBadge
          count={count ?? 0}
          text={text}
          tone="warning"
          label={label}
          placement="corner"
          className="pointer-events-none"
        />
      ) : null}
    </Button>
  );
}

/**
 * THE bell, live. It counts the feed's rows and opens the one panel. It
 * draws nothing for a member who holds no source of the feed.
 */
export function ShellBell({
  onBeforeOpen,
  className = "",
}: {
  /** Runs first: the phone drawer closes itself before the sheet rises. */
  onBeforeOpen?: () => void;
  /** Layout only. */
  className?: string;
}) {
  const { allowed, approvals } = useBellAllowed();
  const needs = useNeedsYou(allowed, approvals);
  const open = useBellOpen();
  if (!allowed) return null;
  return (
    <BellButton
      count={needs.total}
      open={open}
      onOpen={() => {
        onBeforeOpen?.();
        openBell();
      }}
      className={className}
    />
  );
}

// ── The panel ────────────────────────────────────────────────────────────────

/** How often the panel's relative times move while it is open. */
const TICK_MS = 30_000;

/**
 * How often the host reads the feed again while the tab is in view. The
 * Projects bell it replaces read its list once a minute too.
 */
export const BELL_POLL_MS = 60_000;

/**
 * What the panel says under a list the feed cut: "Showing 30 of 45", or
 * nothing when the list is whole.
 */
export function shownOfTotal(shown: number, total: number | undefined): string | null {
  return total !== undefined && total > shown ? `Showing ${shown} of ${total}` : null;
}

/**
 * The panel: a `Modal` around `NeedsList`, as the activity panel beside it
 * is. Focus moves in, Tab stays in it, Escape and an outside press close it,
 * and focus returns to the bell. A row's link closes it as it goes, and so
 * does a Done (see the header: its Undo must be in reach).
 */
export function BellPanel({
  open,
  onClose,
  needs,
  placement = "end",
}: {
  open: boolean;
  onClose: () => void;
  needs: NeedsYou;
  /** `end` under the shell bar's right end, `top` beside a sidebar, `sheet` on a phone. */
  placement?: ModalPlacement;
}) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    if (!open) return;
    // At once on open, then every tick: the panel may have mounted hours ago.
    const tick = () => setNow(new Date());
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, TICK_MS);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [open]);
  const home = homePane();
  // A Done closes the panel FIRST, then runs the one act. No focus moves
  // inside a closing panel (`from` is null): focus goes back to the bell.
  const panelNeeds = useMemo<NeedsYou>(
    () => ({
      ...needs,
      act: (item, from) => {
        if (item.act === "done") {
          onClose();
          needs.act(item, null);
        } else {
          needs.act(item, from);
        }
      },
    }),
    [needs, onClose],
  );
  const cut = shownOfTotal(needs.items?.length ?? 0, needs.total);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Needs you"
      icon="Bell"
      size="sm"
      placement={placement}
      closeLabel="Close the needs list"
      className="max-h-[70vh]"
    >
      {/* `data-needs-panel` is the scope `moveFocusAfterLeave` reads, so a
          row that leaves hands focus to the next row's act. */}
      <div
        data-needs-panel=""
        className="flex min-h-0 flex-1 flex-col"
        onClick={(e) => {
          // A row is a link to its app. The panel goes with the member.
          if ((e.target as Element).closest("a[href]")) onClose();
        }}
      >
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          <NeedsList needs={panelNeeds} now={now} />
        </div>
        <footer className="flex items-center justify-between gap-3 border-t border-border px-4 py-2">
          <Link
            href="/"
            data-leave-focus=""
            className="inline-flex min-h-8 items-center gap-1.5 text-xs font-medium text-primary hover:underline underline-offset-2"
          >
            Open {home.label}
            <Icon name="ArrowRight" size={12} />
          </Link>
          {cut ? (
            <span data-needs-cut="" className="text-xs text-muted-foreground">
              {cut}
            </span>
          ) : null}
        </footer>
      </div>
    </Modal>
  );
}

/**
 * The one panel, for every layout. `AppShell` renders it while the flag is
 * on. It also reads the feed again once a minute while the tab is in view,
 * and once each time the panel opens.
 *
 * It mounts `UndoToastFallback` too. A page that mounts `UndoToast` (My
 * Tasks, the Calendar and My Day, and no other) says the Undo already. On
 * every other page, Projects among them, this says it, so a Done from the
 * bell offers the store's Undo everywhere.
 */
export function ShellBellHost({ placement }: { placement: ModalPlacement }) {
  const { allowed, approvals } = useBellAllowed();
  const needs = useNeedsYou(allowed, approvals);
  const open = useBellOpen();
  const { refresh } = needs;

  useEffect(() => {
    if (!allowed) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") refresh();
    }, BELL_POLL_MS);
    return () => clearInterval(id);
  }, [allowed, refresh]);

  useEffect(() => {
    if (allowed && open) refresh();
  }, [allowed, open, refresh]);

  if (!allowed) return null;
  return (
    <>
      <BellPanel open={open} onClose={closeBell} needs={needs} placement={placement} />
      <UndoToastFallback />
    </>
  );
}
