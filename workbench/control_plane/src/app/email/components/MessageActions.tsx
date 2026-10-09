"use client";

/**
 * The action row of one message — the same row on a single email and on each
 * message of a thread (owner, 2026-10-10).
 *
 * Reply, Reply all and Forward draw as labelled buttons. In dark mode a sun
 * toggles the light version of the body. "More actions" opens a menu of the
 * rest, and each item acts on THIS message: `MessageActions` gets the message
 * and the handlers of the reading pane, and it owns no handler of its own.
 *
 * A narrow card hides the labels first, and then moves Reply all and Forward
 * into the menu (`actionRowTier` in `lib/messageActions.ts`).
 *
 * Keyboard: the menu opens with focus on its first item. Up, Down, Home and
 * End move. Enter or Space picks. Escape closes it and gives focus back to
 * the button. Tab closes it.
 *
 * Fences: `lib/messageActions.test.ts` (the tiers, the groups and the keys)
 * and `e2e/email-message-actions.spec.ts` (the row on both views, the menu
 * by keyboard, and a thread item that acts on its own message).
 */

import Button from "@/components/ui/Button";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Icon from "@/components/Icon";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import { useMode } from "@/lib/theme/surfaces";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { foldersInScope, isRealFolder, useEmailStore } from "../lib/emailStore";
import {
  actionRowTier, lightToggle, lightToggleInRow, menuStep, messageMenuGroups,
  type MenuItemId,
} from "../lib/messageActions";
import type { Email } from "../lib/types";
import { LabelMenu } from "./LabelMenu";

type ReplyMode = "reply" | "reply-all" | "forward";
type Updates = Partial<Pick<Email, "isRead" | "isStarred" | "isFlagged" | "folder">>;

export interface MessageActionsProps {
  /** The message every action acts on. */
  message: Email;
  onReply: (mode: ReplyMode) => void;
  /** The store's `updateEmail` for this message. */
  onUpdate: (updates: Updates) => void;
  onDelete: () => void;
  onTasks: () => void;
  onBlock: () => void;
  onDownload: () => void;
  onActivity: () => void;
  lightVersion: boolean;
  onToggleLight: () => void;
}

const ITEM =
  "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-foreground/85 " +
  "outline-none hover:bg-secondary hover:text-foreground focus-visible:bg-secondary " +
  "focus-visible:text-foreground";

export function MessageActions({
  message, onReply, onUpdate, onDelete, onTasks, onBlock, onDownload, onActivity,
  lightVersion, onToggleLight,
}: MessageActionsProps) {
  const { folders, viewAll, emails } = useEmailStore();
  const dark = useMode() === "dark";
  // The list copy is the live one: a label or a flag set here shows at once.
  const live = emails.find((e) => e.id === message.id) ?? message;

  // ── The tier: measured on the card header, the parent of the row ──
  const rowRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const parent = rowRef.current?.parentElement;
    if (!parent || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(parent);
    return () => observer.disconnect();
  }, []);
  const tier = actionRowTier(width);

  // ── The menu ──
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"main" | "move" | "label">("main");
  const [anchor, setAnchor] = useState<HTMLDivElement | null>(null);
  // The panel draws one frame after it opens. Its list mounts then, and again
  // for each view, so the first item takes focus as the list mounts.
  const focusFirst = useCallback((list: HTMLDivElement | null) => {
    list?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, []);

  const close = useCallback((giveBack: boolean) => {
    setOpen(false);
    setView("main");
    if (giveBack) anchor?.querySelector("button")?.focus();
  }, [anchor]);

  const openMenu = () => {
    setView("main");
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(anchor))) close(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, anchor, close]);

  const showView = (next: "main" | "move" | "label") => setView(next);

  const onMenuKey = (event: React.KeyboardEvent) => {
    const target = event.target as HTMLElement;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
      return;
    }
    if (event.key === "Tab") {
      close(false);
      return;
    }
    // Only between items: an arrow in the label box moves its caret.
    if (target.getAttribute("role") !== "menuitem") return;
    const list = Array.from(
      (event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="menuitem"]'),
    );
    const from = list.indexOf(target);
    const to = menuStep(list.length, from, event.key);
    if (to !== from) {
      event.preventDefault();
      event.stopPropagation();
      list[to]?.focus();
    }
  };

  const run = (id: MenuItemId) => {
    if (id === "move" || id === "label") {
      showView(id);
      return;
    }
    close(id !== "print");
    if (id === "reply-all" || id === "forward") onReply(id);
    else if (id === "tasks") onTasks();
    else if (id === "archive") onUpdate({ folder: "archive" });
    else if (id === "delete") onDelete();
    else if (id === "read") onUpdate({ isRead: !live.isRead });
    else if (id === "flag") onUpdate({ isFlagged: !live.isFlagged });
    else if (id === "star") onUpdate({ isStarred: !live.isStarred });
    // The menu closes first, so the print does not hold it.
    else if (id === "print") setTimeout(() => window.print(), 50);
    else if (id === "download") onDownload();
    else if (id === "light") onToggleLight();
    else if (id === "activity") onActivity();
    else if (id === "block") onBlock();
    else if (id === "junk") onUpdate({ folder: "junk" });
  };

  const groups = messageMenuGroups({
    tier,
    dark,
    lightVersion,
    isRead: live.isRead,
    isFlagged: live.isFlagged,
    isStarred: live.isStarred,
  });

  const labelled = tier === "labels";
  const replyButton = (mode: ReplyMode, label: string, icon: string) => (
    <Button
      variant="ghost"
      size={labelled ? "sm" : "icon-sm"}
      icon={icon}
      type="button"
      title={label}
      aria-label={label}
      onClick={() => onReply(mode)}
    >
      {labelled ? label : null}
    </Button>
  );

  const light = lightToggle(lightVersion);

  return (
    <div
      ref={rowRef}
      data-message-actions={message.id}
      data-tier={tier}
      className="flex flex-shrink-0 items-center gap-0.5"
      // A thread card toggles on a click. A click in the row is not that click.
      onClick={(e) => e.stopPropagation()}
    >
      {replyButton("reply", "Reply", "Reply")}
      {tier !== "collapsed" && replyButton("reply-all", "Reply all", "ReplyAll")}
      {tier !== "collapsed" && replyButton("forward", "Forward", "Forward")}
      {lightToggleInRow({ tier, dark }) && (
        <Button
          variant="ghost"
          size="icon-sm"
          icon={light.icon}
          type="button"
          title={light.label}
          aria-label="View light version"
          selected={lightVersion}
          onClick={onToggleLight}
        />
      )}
      <div ref={setAnchor} className="flex">
        <Button
          variant="ghost"
          size="icon-sm"
          icon="MoreHorizontal"
          type="button"
          title="More actions"
          aria-label="More actions"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => (open ? close(false) : openMenu())}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && !open) {
              e.preventDefault();
              openMenu();
            }
          }}
        />
      </div>
      <AnchoredPanel
        anchor={anchor}
        open={open}
        align="end"
        maxHeight={440}
        className="w-56 p-1"
        panelProps={{ role: "menu", "aria-label": "More actions", onKeyDown: onMenuKey }}
      >
        <div key={view} ref={focusFirst}>
          {view === "main" &&
            groups.map((group, g) => (
              <Fragment key={g}>
                {g > 0 && <div role="separator" className="my-1 h-px bg-border" />}
                {group.map((item) => {
                  return (
                    <button
                      key={item.id}
                      type="button"
                      role="menuitem"
                      data-item={item.id}
                      aria-haspopup={item.submenu ? "menu" : undefined}
                      tabIndex={-1}
                      className={ITEM}
                      onClick={() => run(item.id)}
                    >
                      <Icon name={item.icon} size={13} className="shrink-0 text-muted-foreground" />
                      <span className="min-w-0 flex-1 truncate">{item.label}</span>
                      {item.submenu && <Icon name="ChevronRight" size={12} className="shrink-0 text-muted-foreground" />}
                    </button>
                  );
                })}
              </Fragment>
            ))}
          {view !== "main" && (
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              className={ITEM}
              onClick={() => showView("main")}
            >
              <Icon name="ChevronLeft" size={13} className="shrink-0 text-muted-foreground" />
              <span className="flex-1">{view === "move" ? "Move to" : "Label"}</span>
            </button>
          )}
          {view === "move" &&
            foldersInScope(folders, viewAll)
              .filter((f) => isRealFolder(f.key) && f.key !== live.folder)
              .map((f) => (
                <button
                  key={f.key}
                  type="button"
                  role="menuitem"
                  tabIndex={-1}
                  className={ITEM}
                  onClick={() => {
                    close(true);
                    onUpdate({ folder: f.key });
                  }}
                >
                  <span className="min-w-0 flex-1 truncate pl-5">{f.label}</span>
                </button>
              ))}
          {view === "label" && <LabelMenu email={live} embedded />}
        </div>
      </AnchoredPanel>
    </div>
  );
}
