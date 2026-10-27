/**
 * The tabs of the phone bar on `/projects` (WS-64 OP-4 and OP-11,
 * `one_projects_app.md` §9 OP-4 item 1 and §9 OP-11).
 *
 * The shell draws the first tab of every bar, "Menu", and the Ask tab at the
 * right end. This list is what the Projects branch draws between them.
 *
 * Flag off: Projects, Views, Search, exactly as before. Flag on: My tasks,
 * Capture, Search, so the bar reads Menu, My tasks, Capture, Search, Ask.
 * "Menu" opens the global drawer on every page, flag on or off (supervisor
 * decision 2026-10-25), so a phone member always reaches the other apps.
 * "My tasks" opens the My tasks space on its List (OP-11). A second tap,
 * while My tasks shows, opens the Projects sheet: My work, Personal and
 * Spaces. Each `event` is the detail of a `cc-mobile-nav` event that
 * `app/projects/page.tsx` answers. The event id `projects-mywork` stays, so
 * no listener changes its name. Fence: `tasksRedirect.test.ts`.
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
    { event: "projects-mywork", label: "My tasks", icon: "ListChecks" },
    { event: "projects-capture", label: "Capture", icon: "Plus", accent: true },
    { event: "projects-search", label: "Search", icon: "Search" },
  ];
}
