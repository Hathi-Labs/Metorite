"use client";

/**
 * The guide's "Show me" chip in the side chat (WS-OB GD-4, `assistant_guide.md`
 * §4.1). A run that names a guide where it may not move the page (a phone, the
 * full view) leaves the chip above the message field, and the member taps it
 * (`guideAct` in `assistant.ts`). The assistant lends it a slot (`AgentChat`'s
 * `aboveComposer`) and knows nothing else of the tour.
 *
 * The tour's own card is NOT here. It floats above the dim page (`TourCard` in
 * `GuideSpotlight.tsx`), because inside the chat it mixed in with the chat's
 * own buttons (owner, 2026-11-03). While a tour runs, this draws nothing.
 */

import Button from "@/components/ui/Button";

import { acceptGuideOffer, dismissGuideOffer, isActive, useGuide } from "./guide";

export default function GuideOffer() {
  const g = useGuide();
  if (isActive(g) || !g.offer) return null;
  return (
    <div data-guide-offer="" className="mb-2 flex items-center gap-2">
      <Button variant="secondary" size="sm" icon="Sparkles" onClick={acceptGuideOffer}>
        Show me: {g.offer.title}
      </Button>
      <Button variant="ghost" size="sm" onClick={dismissGuideOffer}>
        Not now
      </Button>
    </div>
  );
}
