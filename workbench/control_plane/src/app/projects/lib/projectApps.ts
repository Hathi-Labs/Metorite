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

export type ProjectAppId = "analytics" | "reports" | "ai-chat";

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
        id: "reports",
        label: "Reports",
        icon: "FileText",
        note: "What gets delivered",
        // §9.12.8 slice 1 renders in the app. Delivery is built and DARK —
        // `PROJECT_REPORT_EMAIL_ENABLED` is off, and arming it is the owner's.
        launch: "live",
      },
      {
        id: "ai-chat",
        label: "AI chat",
        icon: "Sparkles",
        note: "Ask about your work",
        // Built dark (WS-27bm, `specs/projects_ai_chat.md` §4.3). This is the
        // BASE entry; `projectAppSections()` flips it to `live` when
        // `NEXT_PUBLIC_PROJECTS_CHAT` is on. Off, it renders and says so —
        // see the `preview` note above.
        launch: "preview",
      },
    ],
  },
];

/**
 * Is the Projects chat on for this build?
 *
 * An env var rather than a feature grant, for the reason `lens.ts` gives:
 * `preview`/`feature:` slugs say who may reach an app, and this says whether
 * an unfinished surface is shown at all (`launch_surface.md` §2 — "`preview`
 * is not a permission"). The backend agent is registered either way; the
 * main chat app can reach it whether or not this is on.
 */
export function chatEnabled(
  env?: Record<string, string | undefined>,
): boolean {
  // ⚠️ The LITERAL member expression is the only form Next inlines into the
  // browser bundle. `env.NEXT_PUBLIC_X` off a defaulted `env = process.env`
  // is NOT inlined: in a browser `process.env` is the `{}` polyfill, so the
  // flag reads undefined and the slot never goes live — measured in this
  // repo's own build output on 2026-09-22. Tests pass an env object; the
  // page passes nothing and hits the literal. `projectApps.test.ts` holds
  // this file to the literal spelling.
  const raw = env ? env.NEXT_PUBLIC_PROJECTS_CHAT : process.env.NEXT_PUBLIC_PROJECTS_CHAT;
  return raw === "1" || raw === "true" || raw === "on";
}

/**
 * The sections the sidebar draws, with the flagged entries resolved.
 *
 * `PROJECT_APP_SECTIONS` stays the shape of record and the thing tests
 * enumerate. This is what the page renders, so a flag changes exactly one
 * word on exactly one entry and nothing else about the list.
 */
export function projectAppSections(
  env?: Record<string, string | undefined>,
): ProjectAppSection[] {
  const chat = chatEnabled(env);
  return PROJECT_APP_SECTIONS.map((section) => ({
    ...section,
    items: section.items.map((item) =>
      item.id === "ai-chat" && chat ? { ...item, launch: "live" } : item,
    ),
  }));
}

/** The heading the space tree sits under. */
export const SPACES_SECTION_LABEL = "Spaces";
