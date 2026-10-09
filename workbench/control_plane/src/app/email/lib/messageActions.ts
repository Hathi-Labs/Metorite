/**
 * The action row of one message in the reading pane (owner, 2026-10-10).
 *
 * The owner said: "When an email is part of a thread, reply, reply-all, and
 * similar buttons are always visible. In a single email, those options are
 * missing." Every message card now has the same row: Reply, Reply all,
 * Forward, the light-version toggle in dark mode, and a "More actions" menu.
 * Each item acts on THAT message, not on the open conversation.
 *
 * The rules are pure functions here, because `vitest.config.ts` runs in
 * `node` and cannot render `MessageActions.tsx`. Fence:
 * `messageActions.test.ts`.
 */

/** How much of the row fits. `collapsed` moves Reply all and Forward into the menu. */
export type RowTier = "labels" | "icons" | "collapsed";

/** The width of the card header, in px, from which each tier fits. */
export const LABELS_MIN_PX = 600;
export const ICONS_MIN_PX = 360;

/** The tier for a card header `width` px wide. Zero means not measured yet. */
export function actionRowTier(width: number): RowTier {
  if (width <= 0 || width >= LABELS_MIN_PX) return "labels";
  if (width >= ICONS_MIN_PX) return "icons";
  return "collapsed";
}

export type MenuItemId =
  | "reply-all"
  | "forward"
  | "tasks"
  | "archive"
  | "delete"
  | "move"
  | "label"
  | "read"
  | "flag"
  | "star"
  | "print"
  | "download"
  | "light"
  | "activity"
  | "block"
  | "junk";

export interface MenuItem {
  id: MenuItemId;
  label: string;
  /** A Lucide name, drawn through `<Icon>`. */
  icon: string;
  /** True when the item opens a second list in the same panel. */
  submenu?: boolean;
}

/** What the menu needs to know about the message and the screen. */
export interface MenuState {
  tier: RowTier;
  /** The app is in dark mode, so the light version means something. */
  dark: boolean;
  lightVersion: boolean;
  isRead: boolean;
  isFlagged: boolean;
  isStarred: boolean;
}

/** The light-version toggle's words and icon. */
export function lightToggle(lightVersion: boolean): { label: string; icon: string } {
  return lightVersion
    ? { label: "View dark version", icon: "Moon" }
    : { label: "View light version", icon: "Sun" };
}

/** True when the light-version toggle draws in the row, not in the menu. */
export function lightToggleInRow(s: Pick<MenuState, "tier" | "dark">): boolean {
  return s.dark && s.tier !== "collapsed";
}

/**
 * The groups of the "More actions" menu, top to bottom. A divider draws
 * between two groups. The order is the owner's list of 2026-10-10.
 */
export function messageMenuGroups(s: MenuState): MenuItem[][] {
  const groups: MenuItem[][] = [];
  if (s.tier === "collapsed") {
    groups.push([
      { id: "reply-all", label: "Reply all", icon: "ReplyAll" },
      { id: "forward", label: "Forward", icon: "Forward" },
    ]);
  }
  groups.push([{ id: "tasks", label: "Add to My Tasks", icon: "ListChecks" }]);
  groups.push([
    { id: "archive", label: "Archive", icon: "Archive" },
    { id: "delete", label: "Delete", icon: "Trash2" },
    { id: "move", label: "Move to…", icon: "FolderInput", submenu: true },
    { id: "label", label: "Label…", icon: "Tag", submenu: true },
  ]);
  groups.push([
    s.isRead
      ? { id: "read", label: "Mark as unread", icon: "Mail" }
      : { id: "read", label: "Mark as read", icon: "MailOpen" },
    { id: "flag", label: s.isFlagged ? "Unflag" : "Flag", icon: "Flag" },
    { id: "star", label: s.isStarred ? "Unstar" : "Star", icon: "Star" },
  ]);
  groups.push([
    { id: "print", label: "Print", icon: "Printer" },
    { id: "download", label: "Download (.eml)", icon: "Download" },
  ]);
  const view: MenuItem[] = [{ id: "activity", label: "View activity", icon: "History" }];
  if (s.dark && !lightToggleInRow(s)) view.unshift({ id: "light", ...lightToggle(s.lightVersion) });
  groups.push(view);
  groups.push([
    { id: "block", label: "Block sender", icon: "Ban" },
    { id: "junk", label: "Report spam / phishing", icon: "ShieldAlert" },
  ]);
  return groups;
}

/**
 * Where focus goes in a menu of `count` items after `key`, from `from`.
 * Up and Down wrap. Home and End go to the ends. Any other key leaves it.
 */
export function menuStep(count: number, from: number, key: string): number {
  if (count <= 0) return -1;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  if (key === "ArrowDown") return (from + 1 + count) % count;
  if (key === "ArrowUp") return (from - 1 + count) % count;
  return from;
}
