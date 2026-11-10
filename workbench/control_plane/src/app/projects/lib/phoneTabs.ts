/**
 * The tabs of the phone bar on `/projects` (WS-64 OP-4 and OP-11,
 * `one_projects_app.md` §9 OP-4 item 1 and §9 OP-11).
 *
 * The shell draws the first tab of every bar, "Menu", and the Ask tab at the
 * right end. This list is what the Projects branch draws between them.
 *
 * Flag off: Projects, Views, Search, exactly as before. Flag on: Projects,
 * Capture, Search, so the bar reads Menu, Projects, Capture, Search, Ask.
 * "Menu" opens the global drawer on every page, flag on or off (supervisor
 * decision 2026-10-25), so a phone member always reaches the other apps.
 * "Projects" opens the Projects sheet at once (MOB-10, task 2003): My task
 * inbox, My tasks, Reports, Personal and Spaces. A tab called "My tasks"
 * read as the member's own list, and the owner wants every task of the
 * company one tap away. My tasks stays a row in that sheet. The registry of the
 * bar (`lib/shell/phoneBar.ts`) reads this list. Each `event` is
 * the detail of a `cc-mobile-nav` event that `app/projects/page.tsx`
 * answers. Fence: `tasksRedirect.test.ts`.
 */

export interface ProjectsPhoneTab {
  event: string;
  label: string;
  icon: string;
  /** A place goes somewhere and shows selected. An act does one thing (MOB-12). */
  kind: "place" | "act";
  /** Drawn in the primary colour, like Capture on `/tasks`. */
  accent?: boolean;
}

export function projectsPhoneTabs(myWork: boolean): ProjectsPhoneTab[] {
  if (!myWork) {
    return [
      { event: "projects-tree", label: "Projects", icon: "FolderKanban", kind: "place" },
      { event: "projects-views", label: "Views", icon: "LayoutGrid", kind: "act" },
      { event: "projects-search", label: "Search", icon: "Search", kind: "act" },
    ];
  }
  return [
    { event: "projects-tree", label: "Projects", icon: "FolderKanban", kind: "place" },
    { event: "projects-capture", label: "Capture", icon: "Plus", kind: "act", accent: true },
    { event: "projects-search", label: "Search", icon: "Search", kind: "act" },
  ];
}
