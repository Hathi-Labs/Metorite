"use client";

/**
 * /studio — the AI Studio library (`specs/ai_studio.md` §13, §14 A1 + E1).
 *
 * One library of apps, workflows and skills. A kind filter narrows it, and
 * the choice lives in the URL (`?kind=apps|workflows|skills`). Module Studio
 * is a second tab (`?tab=modules`).
 *
 * The page owns the one page heading, through `AppTopBar`. The sections under it
 * head themselves with an `<h2>`. The Apps section shows only to a member who
 * holds `build.apps`. The Workflows pill, the Workflows section and the Module
 * Studio tab show only to a member who holds `workflows`, because their API
 * keeps that feature (J1-1). The Module Studio tab also needs the org's
 * switch `MODULE_RUN_ORGS`, which the shell read says as `module_runs`
 * (STU-Q15a). `studioLibraryView` decides all of it. The Skills
 * section reads the list of the skill creator
 * (STU-F2b). An org outside `STUDIO_ORGS` gets 404, and then that section
 * keeps a plain empty state, with no button and no error.
 *
 * Data:  apps       GET /api/apps       (AppsGallery)
 *        workflows  GET /api/workflows  (WorkflowsGallery)
 *        skills     GET /api/studio/skills (SkillsGallery)
 *        modules    /api/workflows/modules* (ModuleStudio)
 */

import { Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useAccess } from "@/components/AccessProvider";
import { AppTopBar } from "@/components/AppTopBar";
import FilterPills from "@/components/FilterPills";
import Tabs from "@/components/Tabs";
import ModuleStudio from "@/app/workflows/components/ModuleStudio";
import { useLiveRefresh } from "@/lib/liveRefresh";
import { useModuleRunsOn } from "@/lib/shell/shellPrefs";
import { studioLibraryView, type StudioKind } from "@/lib/studio/library";
import AppsGallery from "./components/AppsGallery";
import SkillsGallery from "./components/SkillsGallery";
import WorkflowsGallery from "./components/WorkflowsGallery";

/** The raised panel every library section sits on (DESIGN_SYSTEM §3 `bg-card`). */
const PANEL = "rounded-xl border border-border bg-card overflow-hidden shrink-0";

function StudioLibrary() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { access } = useAccess();
  const moduleRuns = useModuleRunsOn();
  // An app or skill that the assistant saves in chat shows with no reload: the
  // bumped key remounts each gallery, and each one reads its list again.
  const [librarySeq, setLibrarySeq] = useState(0);
  useLiveRefresh(() => setLibrarySeq((n) => n + 1), "studio");

  const view = studioLibraryView(access, params.get("kind"), params.get("tab"), moduleRuns);

  function go(next: { kind?: StudioKind; tab?: string }) {
    const q = new URLSearchParams(params.toString());
    if (next.kind !== undefined) {
      if (next.kind === "all") q.delete("kind");
      else q.set("kind", next.kind);
    }
    if (next.tab !== undefined) {
      if (next.tab === "library") q.delete("tab");
      else q.set("tab", next.tab);
    }
    const qs = q.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname);
  }

  return (
    <div className="flex flex-col h-full">
      <AppTopBar title="Studio" subtitle="Apps, workflows and skills in one library" />

      {view.tabs.length > 1 && (
        <Tabs tabs={view.tabs} activeTab={view.tab} onTabChange={(id) => go({ tab: id })} />
      )}

      {view.tab === "modules" ? (
        <ModuleStudio />
      ) : (
        <>
          <FilterPills
            items={view.kindItems}
            activeId={view.activeKind}
            onChange={(id) => go({ kind: id as StudioKind })}
          />
          <div className="flex-1 overflow-y-auto p-3 sm:p-4 flex flex-col gap-3 sm:gap-4">
            {view.showApps && <div key={`apps-${librarySeq}`} className={PANEL}><AppsGallery /></div>}
            {view.showWorkflows && <div key={`workflows-${librarySeq}`} className={PANEL}><WorkflowsGallery /></div>}
            {view.showSkills && <div key={`skills-${librarySeq}`} className={PANEL}><SkillsGallery /></div>}
          </div>
        </>
      )}
    </div>
  );
}

export default function StudioPage() {
  return (
    <Suspense fallback={null}>
      <StudioLibrary />
    </Suspense>
  );
}
