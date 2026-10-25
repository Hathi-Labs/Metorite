/**
 * The tabs of the phone bar on `/projects` (WS-64 OP-4,
 * `one_projects_app.md` §9 OP-4 item 1).
 *
 * The shell draws the first tab of every bar, "Menu", and the Ask tab at the
 * right end. This list is what the Projects branch draws between them.
 *
 * Flag off: Projects, Views, Search, exactly as before. Flag on: My work,
 * Capture, Search, so the bar reads Menu, My work, Capture, Search, Ask.
 * "Menu" opens the global drawer on every page, flag on or off (supervisor
 * decision 2026-10-25), so a phone member always reaches the other apps. "My
 * work" opens the Projects sheet. Each `event` is the detail of a `cc-mobile-nav` event
 * that `app/projects/page.tsx` answers. Fence: `tasksRedirect.test.ts`.
 */

export interface ProjectsPhoneTab {
  event: string;
  label: string;
  icon: string;
  /** Drawn in the primary colour, like Capture on `/tasks`. */
  accent?: boolean;
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
