"use client";

/** The state of a skill as a pill. The hue comes from `statusAccent.ts`. */

import { accentForHue } from "@/lib/statusAccent";
import { SKILL_STATE_HUE } from "@/lib/studio/skillForm";

export default function SkillStateBadge({ state }: { state: string }) {
  const accent = accentForHue(SKILL_STATE_HUE[state] ?? "gray");
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[10px] font-medium ${accent.chip}`}>
      {state}
    </span>
  );
}
