"use client";

/**
 * /studio — the AI Studio library (`specs/ai_studio.md` §13, §14 A1 + E1).
 *
 * One library of apps, workflows and skills. A kind filter narrows it, and
 * the choice lives in the URL (`?kind=apps|workflows|skills`). Module Studio
 * is a second tab (`?tab=modules`).
 *
 * The page owns the one `<h1>`, through `AppTopBar`. The sections under it
 * head themselves with an `<h2>`. The Apps section shows only to a member who
 * holds `build.apps`. Skills have no data yet, so that section is a plain
 * empty state and never a fake row.
 *
 * Data:  apps       GET /api/apps       (AppsGallery)
 *        workflows  GET /api/workflows  (WorkflowsGallery)
 *        modules    /api/workflows/modules* (ModuleStudio)
 */

import { Suspense } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useAccess } from "@/components/AccessProvider";
import { AppTopBar } from "@/components/AppTopBar";
import { EmptyState } from "@/components/EmptyState";
import FilterPills from "@/components/FilterPills";
import Tabs from "@/components/Tabs";
import ModuleStudio from "@/app/workflows/components/ModuleStudio";
import { hasCapability } from "@/lib/access";
import AppsGallery from "./components/AppsGallery";
import WorkflowsGallery from "./components/WorkflowsGallery";

const KINDS = ["all", "apps", "workflows", "skills"] as const;
type Kind = (typeof KINDS)[number];

function readKind(raw: string | null): Kind {
  return (KINDS as readonly string[]).includes(raw ?? "") ? (raw as Kind) : "all";
}

function StudioLibrary() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { access } = useAccess();

  const kind = readKind(params.get("kind"));
  const tab = params.get("tab") === "modules" ? "modules" : "library";
  const canBuildApps = hasCapability(access, "build.apps");

  function go(next: { kind?: Kind; tab?: string }) {
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

  const kindItems = [
    { id: "all", label: "All" },
    ...(canBuildApps ? [{ id: "apps", label: "Apps" }] : []),
    { id: "workflows", label: "Workflows" },
    { id: "skills", label: "Skills" },
  ];
  const showApps = canBuildApps && (kind === "all" || kind === "apps");
  const showWorkflows = kind === "all" || kind === "workflows";
  const showSkills = kind === "all" || kind === "skills";

  return (
    <div className="flex flex-col h-full">
      <AppTopBar title="Studio" subtitle="Apps, workflows and skills in one library" />

      <Tabs
        tabs={[
          { id: "library", label: "Library", icon: "LayoutGrid" },
          { id: "modules", label: "Module Studio", icon: "Boxes" },
        ]}
        activeTab={tab}
        onTabChange={(id) => go({ tab: id })}
      />

      {tab === "modules" ? (
        <ModuleStudio />
      ) : (
        <>
          <FilterPills
            items={kindItems}
            activeId={kind === "apps" && !canBuildApps ? "all" : kind}
            onChange={(id) => go({ kind: id as Kind })}
          />
          <div className="flex-1 overflow-y-auto">
            {showApps && <AppsGallery />}
            {showWorkflows && <WorkflowsGallery />}
            {showSkills && (
              <section aria-label="Skills" className="flex flex-col">
                <div className="px-4 sm:px-6 py-3 border-b border-border">
                  <h2 className="text-sm font-bold text-foreground">Skills</h2>
                </div>
                <EmptyState
                  icon="Sparkles"
                  message="Skills arrive with the skill creator."
                  hint="Your skills will list here with your apps and workflows."
                />
              </section>
            )}
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
