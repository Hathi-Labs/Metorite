/**
 * The Projects sidebar's own sections — the app's internal navigation.
 *
 * Owner directive 2026-08-31: *"follow the same kind of structure as the
 * main Metorite application sidebar for the project sidebar itself"*, with
 * the app-level destinations on top and a **Spaces** section beneath them.
 *
 * ⚠️ **The SHAPE is borrowed, the DATA is not.** This mirrors
 * `src/lib/nav.ts`'s section→item grammar so the two sidebars read as one
 * product, but it is a separate list on purpose: these are destinations
 * *inside* one app, they are not routes, they carry no `feature` grant, and
 * they never belong in `PANES` — `nav.test.ts` asserts `PANES` equals the
 * launch-surface allowlist, so adding them there would fail it and would
 * also put "Analytics" in the global nav, which is not what it is.
 *
 * ⚠️ **`preview` means NOT BUILT, never "hidden by permission"** — the same
 * word `launch_surface.md` uses, and the same rule: a preview entry is
 * visible, disabled, and honest about it. A nav entry that looks live and
 * does nothing is worse than no entry.
 */

export type ProjectAppId = "analytics" | "ai-chat";

export interface ProjectAppItem {
  id: ProjectAppId;
  label: string;
  /** A themed icon NAME — the active pack draws it (AGENTS.md rule 2). */
  icon: string;
  /** One line under the label, as `nav.ts` panes carry. */
  note?: string;
  /** 'live' is reachable. 'preview' renders, disabled, saying so. */
  launch: "live" | "preview";
}

export interface ProjectAppSection {
  id: string;
  /** Omitted = an unheaded group, like the main sidebar's first block. */
  label?: string;
  items: ProjectAppItem[];
}

export const PROJECT_APP_SECTIONS: ProjectAppSection[] = [
  {
    id: "workspace",
    items: [
      // "My work" was REMOVED here (owner directive 2026-08-31): /tasks IS
      // the personal lens over the one store (D52-D54), so an entry for it
      // inside Projects was a second door to the same room.
      {
        id: "analytics",
        label: "Analytics",
        icon: "BarChart3",
        note: "Every space at a glance",
        launch: "live",
      },
      {
        id: "ai-chat",
        label: "AI chat",
        icon: "Sparkles",
        note: "Ask about your work",
        // Not built. It renders and says so — see the `preview` note above.
        launch: "preview",
      },
    ],
  },
];

/** The heading the space tree sits under. */
export const SPACES_SECTION_LABEL = "Spaces";
