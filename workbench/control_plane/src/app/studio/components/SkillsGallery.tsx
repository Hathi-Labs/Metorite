"use client";

/**
 * The Skills section of the Studio library (`ai_studio.md` §14 F2b).
 *
 * Data:  list  GET /api/studio/skills
 *
 * The list answers 404 for every org that `STUDIO_ORGS` does not list, and
 * that is the normal answer today. A 404, a 401 and a miss of the network keep
 * the plain empty state, with no button and no error (`viewOfList`). A list
 * with rows shows them, and a New skill button opens the form. The Studio
 * page owns the one page heading.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { EmptyState } from "@/components/EmptyState";
import Button from "@/components/ui/Button";
import { loadSkillsView, type SkillsView } from "@/lib/studio/skillsApi";
import SkillStateBadge from "./SkillStateBadge";

export default function SkillsGallery() {
  const router = useRouter();
  const [view, setView] = useState<SkillsView | null>(null);

  /* eslint-disable react-hooks/set-state-in-effect */
  const load = useCallback(async () => {
    setView(await loadSkillsView());
  }, []);
  useEffect(() => {
    load();
  }, [load]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const on = view?.kind === "ok";
  const rows = view?.kind === "ok" ? view.skills : [];

  return (
    <section aria-label="Skills" className="flex flex-col">
      <div className="flex items-center justify-between gap-2 px-4 sm:px-6 py-3 border-b border-border">
        <h2 className="text-sm font-bold text-foreground">Skills</h2>
        {on && rows.length > 0 && (
          <Button size="md" href="/studio/skills/new" icon="Plus">
            New skill
          </Button>
        )}
      </div>

      {view?.kind === "error" && (
        <div className="flex flex-col items-center gap-2 p-6 text-center">
          <p role="alert" className="text-sm text-destructive">{view.detail}</p>
          <Button variant="secondary" size="md" onClick={load}>Retry</Button>
        </div>
      )}

      {on && rows.length > 0 && (
        <ul className="flex flex-col divide-y divide-border">
          {rows.map((skill) => (
            <li key={skill.id}>
              <Link
                href={`/studio/skills/${skill.id}`}
                className="cc-control flex flex-wrap items-center gap-x-3 gap-y-1 px-4 sm:px-6 py-3 hover:bg-secondary"
              >
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{skill.name}</span>
                <SkillStateBadge state={skill.state} />
                <span className="text-xs text-muted-foreground">
                  {skill.current_version ? `Version ${skill.current_version}` : "No version"}
                </span>
                {skill.description && (
                  <span className="basis-full truncate text-xs text-muted-foreground">{skill.description}</span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}

      {(view?.kind === "off" || (on && rows.length === 0)) && (
        <EmptyState
          icon="Sparkles"
          message={on ? "No skills yet." : "Skills arrive with the skill creator."}
          hint={on ? "Make one, and it lists here with your apps and workflows." : "Your skills will list here with your apps and workflows."}
          action={on ? { label: "New skill", icon: "Plus", onClick: () => router.push("/studio/skills/new") } : undefined}
        />
      )}
    </section>
  );
}
