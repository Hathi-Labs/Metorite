"use client";

/**
 * The mark of a recommended option, in ONE shape for every surface that
 * recommends: the `optionPicker` and `comparison` templates
 * (`genUITemplates.tsx`) and `ElicitationCard`.
 *
 * Owner report, 2026-10-09: a bare star did not say "recommended". So the
 * mark says the word, on the shared `Badge`.
 *
 * The tone is the accent, `primary`, with no icon (owner report,
 * 2026-10-13). The yellow star pill looked "out of place" beside the other
 * chat cards: a second hue and a second mark for one word. The word says
 * it, in the member's own accent, like every other chip of a card. In the
 * templates, the border of a recommended option takes the same family
 * (`RECOMMENDED_RING`), so the option and its mark read as one thing.
 *
 * Fences: `genUITemplates.test.ts` (the picker and the comparison draw the
 * word) and `elicitationCard.test.ts` (the question card draws it).
 */

import Badge from "@/components/ui/Badge";

export const RECOMMENDED_LABEL = "Recommended";

/** The border of a recommended option, as a CSS value (the templates). */
export const RECOMMENDED_RING = "color-mix(in srgb, var(--primary) 40%, var(--border))";

export default function RecommendedBadge() {
  return (
    <Badge tone="primary" size="xs">
      {RECOMMENDED_LABEL}
    </Badge>
  );
}
