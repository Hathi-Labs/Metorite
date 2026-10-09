"use client";

/**
 * The mark of a recommended option, in ONE shape for every surface that
 * recommends: the `optionPicker` and `comparison` templates
 * (`genUITemplates.tsx`) and `ElicitationCard`.
 *
 * Owner report, 2026-10-09: a bare star did not say "recommended". So the
 * mark says the word, on the shared `Badge`.
 *
 * The tone is `warning`. DESIGN_SYSTEM.md §7 makes `warning` the tone of a
 * highlight, and `contrast.test.ts` measures `text-warning` on its own 10%
 * tint in both modes, so the word clears AA. In the templates, the border
 * of a recommended option takes the same family (`RECOMMENDED_RING`), so
 * the option and its mark read as one thing at a glance.
 *
 * Fences: `genUITemplates.test.ts` (the picker and the comparison draw the
 * word) and `elicitationCard.test.ts` (the question card draws it).
 */

import Badge from "@/components/ui/Badge";

export const RECOMMENDED_LABEL = "Recommended";

/** The border of a recommended option, as a CSS value (the templates). */
export const RECOMMENDED_RING = "color-mix(in srgb, var(--warning) 45%, var(--border))";

export default function RecommendedBadge() {
  return (
    <Badge tone="warning" size="xs" icon="Star">
      {RECOMMENDED_LABEL}
    </Badge>
  );
}
