/**
 * What the Studio library page (`/studio`) shows to one member
 * (`specs/ai_studio.md` §13, §14 A1, E1 and J1).
 *
 * The page gate is `feature:studio` (J1). The parts of the page that call
 * other APIs keep the feature of that API: Apps needs `build.apps`, and the
 * Workflows pill, the Workflows section and the Module Studio tab need
 * `workflows`. A pure function, so a test can hold each case with no render.
 * Fence: `library.test.ts`.
 */
import { canUseFeature, type Access } from "@/lib/access";
import type { TabDef } from "@/components/Tabs";

export const STUDIO_KINDS = ["all", "apps", "workflows", "skills"] as const;
export type StudioKind = (typeof STUDIO_KINDS)[number];
export type StudioTab = "library" | "modules";

export interface StudioLibraryView {
  /** The tab that the page draws. */
  tab: StudioTab;
  /** The tab row. One tab draws no row. */
  tabs: TabDef[];
  kindItems: { id: StudioKind; label: string }[];
  /** The pill that the page marks, after a fallback. */
  activeKind: StudioKind;
  showApps: boolean;
  showWorkflows: boolean;
  showSkills: boolean;
}

function readKind(raw: string | null): StudioKind {
  return (STUDIO_KINDS as readonly string[]).includes(raw ?? "") ? (raw as StudioKind) : "all";
}

/** The view of the library for *access*, from the raw `?kind=` and `?tab=`. */
export function studioLibraryView(
  access: Access,
  rawKind: string | null,
  rawTab: string | null,
): StudioLibraryView {
  const canBuildApps = canUseFeature(access, "build.apps");
  // The Workflows pill, section and Module Studio tab call `/api/workflows`,
  // and that API keeps `feature:workflows` (J1-1).
  const canWorkflows = canUseFeature(access, "workflows");
  const requested = readKind(rawKind);
  const activeKind: StudioKind =
    (requested === "apps" && !canBuildApps) || (requested === "workflows" && !canWorkflows)
      ? "all"
      : requested;
  const tab: StudioTab = rawTab === "modules" && canWorkflows ? "modules" : "library";
  const tabs: TabDef[] = canWorkflows
    ? [
        { id: "library", label: "Library", icon: "LayoutGrid" },
        { id: "modules", label: "Module Studio", icon: "Boxes" },
      ]
    : [];
  return {
    tab,
    tabs,
    kindItems: [
      { id: "all", label: "All" },
      ...(canBuildApps ? [{ id: "apps" as const, label: "Apps" }] : []),
      ...(canWorkflows ? [{ id: "workflows" as const, label: "Workflows" }] : []),
      { id: "skills", label: "Skills" },
    ],
    activeKind,
    showApps: canBuildApps && (activeKind === "all" || activeKind === "apps"),
    showWorkflows: canWorkflows && (activeKind === "all" || activeKind === "workflows"),
    showSkills: activeKind === "all" || activeKind === "skills",
  };
}
