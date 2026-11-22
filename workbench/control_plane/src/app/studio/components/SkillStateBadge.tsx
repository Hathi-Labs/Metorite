"use client";

/** The state of a skill as a pill. It is the shared `Badge`, in the tone of the state. */

import Badge, { type BadgeTone } from "@/components/ui/Badge";
import { SKILL_STATE_HUE } from "@/lib/studio/skillForm";

const TONE: Record<string, BadgeTone> = { gray: "neutral", amber: "warning", green: "success" };

export default function SkillStateBadge({ state }: { state: string }) {
  return <Badge tone={TONE[SKILL_STATE_HUE[state] ?? "gray"]}>{state}</Badge>;
}
