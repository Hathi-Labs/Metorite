/**
 * The tabs of the phone bar on `/projects`, before the Ask tab that the shell
 * adds at the right end (WS-64 OP-4, `one_projects_app.md` §9 OP-4 item 1).
 *
 * Flag off: Projects, Views, Search, exactly as before. Flag on: Menu, My work,
 * Capture, Search. Each `event` is the detail of a `cc-mobile-nav` event that
 * `app/projects/page.tsx` answers. The first entry is always the tree.
 * Fence: `phoneTabs.test.ts`.
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
    { event: "projects-tree", label: "Menu", icon: "FolderKanban" },
    { event: "projects-mywork", label: "My work", icon: "CalendarDays" },
    { event: "projects-capture", label: "Capture", icon: "Plus", accent: true },
    { event: "projects-search", label: "Search", icon: "Search" },
  ];
}
