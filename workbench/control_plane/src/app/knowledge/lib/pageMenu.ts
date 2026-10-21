// ── The page menu (WS-61 KB-2d, `knowledge_base.md` §21.7) ─────────────────
//
// Pure. The `…` of a page is ONE array of entries. Each entry names when it
// shows, and the view draws what `pageMenuItems` gives back, in order, with
// a line between two groups. KB-2f adds Favourite and KB-2g adds Page history
// as one entry each here, and one act each in `PageMenu.tsx`.
//
// ⚠️ Hiding is a courtesy, never the boundary. The gateway refuses a move
// from a reader with 403, and an archive or a restore from a member without
// manage on the space (`pages.py`).

/** What the menu knows about the page and the member. */
export interface PageMenuState {
  /** The member can write in the page's space (`can_write`). */
  canWrite: boolean;
  /** The member manages the page's space (`level === "manage"`). */
  canManage: boolean;
  archived: boolean;
  /** An edit is open on the page. */
  editing: boolean;
  /** The page draws at the full width of the window. */
  fullWidth: boolean;
}

/** The groups of the menu. A line separates two groups. */
export type PageMenuGroup = "share" | "view" | "organise" | "lifecycle";

export interface PageMenuEntry {
  id: string;
  label: string;
  /** An icon name of the set (`<Icon name>`). */
  icon: string;
  group: PageMenuGroup;
  danger?: boolean;
  /** For a switch: whether it is on. */
  checked?: (s: PageMenuState) => boolean;
  /** When the entry shows. Absent: always. */
  when?: (s: PageMenuState) => boolean;
}

/** The entries of the page menu, in order. Add one line for a new entry. */
export const PAGE_MENU: readonly PageMenuEntry[] = [
  { id: "copy-link", label: "Copy link", icon: "Link", group: "share" },
  { id: "copy-markdown", label: "Copy as Markdown", icon: "Copy", group: "share" },
  { id: "download-md", label: "Download as .md", icon: "Download", group: "share" },
  { id: "print", label: "Print", icon: "Printer", group: "share", when: (s) => !s.editing },
  { id: "full-width", label: "Full width", icon: "MoveHorizontal", group: "view", checked: (s) => s.fullWidth },
  { id: "move", label: "Move", icon: "FolderInput", group: "organise", when: (s) => s.canWrite && !s.archived && !s.editing },
  {
    id: "archive",
    label: "Archive",
    icon: "Archive",
    group: "lifecycle",
    danger: true,
    when: (s) => s.canManage && !s.archived && !s.editing,
  },
  { id: "restore", label: "Restore", icon: "ArchiveRestore", group: "lifecycle", when: (s) => s.canManage && s.archived },
];

/** One row of the drawn menu: an entry, or a line between two groups. */
export type PageMenuRow = { kind: "entry"; entry: PageMenuEntry; checked?: boolean } | { kind: "sep" };

/** The rows the member sees, with a line where the group changes. */
export function pageMenuItems(state: PageMenuState, entries: readonly PageMenuEntry[] = PAGE_MENU): PageMenuRow[] {
  const rows: PageMenuRow[] = [];
  let group: PageMenuGroup | null = null;
  for (const entry of entries) {
    if (entry.when && !entry.when(state)) continue;
    if (group !== null && entry.group !== group) rows.push({ kind: "sep" });
    group = entry.group;
    rows.push({ kind: "entry", entry, checked: entry.checked ? entry.checked(state) : undefined });
  }
  return rows;
}

/** The name of a page's Markdown file: its title, cut to a safe name. */
export function markdownFileName(title: string): string {
  const base = title
    .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120)
    .trim();
  return `${base || "Untitled page"}.md`;
}

/** The Markdown of a page: its title as a `#` heading, then its body. */
export function pageMarkdown(page: { title: string; body_md: string }): string {
  const body = page.body_md.trim();
  return body ? `# ${page.title.trim()}\n\n${body}\n` : `# ${page.title.trim()}\n`;
}
