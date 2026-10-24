/**
 * The tabs of the phone bar on `/projects` (WS-64 OP-4,
 * `one_projects_app.md` §9 OP-4 item 1).
 *
 * The shell draws the first tab of every bar, "Menu", and the Ask tab at the
 * right end. This list is what the Projects branch draws between them.
 *
 * Flag off: Projects, Views, Search, exactly as before, and "Menu" opens the
 * shell drawer. Flag on: My work, Capture, Search, and "Menu" fires
 * `projects-tree` (`projectsMenuEvent`), so the bar reads Menu, My work,
 * Capture, Search, Ask. Each `event` is the detail of a `cc-mobile-nav` event
 * that `app/projects/page.tsx` answers. Fence: `tasksRedirect.test.ts`.
 */

export interface ProjectsPhoneTab {
  event: string;
  label: string;
  icon: string;
  /** Drawn in the primary colour, like Capture on `/tasks`. */
  accent?: boolean;
}

/** The event the shell's own "Menu" tab fires on `/projects`, or null for the drawer. */
export function projectsMenuEvent(myWork: boolean): string | null {
  return myWork ? "projects-tree" : null;
}

export function projectsPhoneTabs(myWork: boolean): ProjectsPhoneTab[] {
  if (!myWork) {
    return [
      { event: "projects-tree", label: "Projects", icon: "FolderKanban" },
      { event: "projects-views", label: "Views", icon: "LayoutGrid" },
      { event: "projects-search", label: "Search", icon: "Search" },
    ];
  }
  return [
    { event: "projects-mywork", label: "My work", icon: "CalendarDays" },
    { event: "projects-capture", label: "Capture", icon: "Plus", accent: true },
    { event: "projects-search", label: "Search", icon: "Search" },
  ];
}
