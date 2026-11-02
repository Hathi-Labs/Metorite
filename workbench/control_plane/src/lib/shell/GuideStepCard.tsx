"use client";

/**
 * The guide's step card in the side chat (WS-OB GD-4, `assistant_guide.md`
 * §4.1). It sits above the message field, so the field stays open and usable.
 * The assistant lends it a slot (`AgentChat`'s `aboveComposer`) and knows
 * nothing else of the tour.
 *
 * It also draws the "Show me" chip. A run that names a guide where it may not
 * move the page (a phone, the full view) leaves the chip, and the member taps
 * it (`guideAct` in `assistant.ts`).
 */

import Button from "@/components/ui/Button";

import {
  ANCHOR_MISSING_NOTE,
  acceptGuideOffer,
  backStep,
  dismissGuideOffer,
  endGuide,
  guideView,
  isActive,
  nextStep,
  useGuide,
  type GuideView,
} from "./guide";
import { stepLabel } from "./GuideSpotlight";

/** The card's content. Exported so a test reads its markup. */
export function GuideStepCardBody(p: {
  view: Pick<GuideView, "say" | "index" | "total" | "missing">;
  onBack?: () => void;
  onNext: () => void;
  onEnd: () => void;
}) {
  return (
    <>
      <p className="text-sm text-foreground">{p.view.say}</p>
      {p.view.missing ? <p className="mt-1 text-xs text-muted-foreground">{ANCHOR_MISSING_NOTE}</p> : null}
      <div className="mt-3 flex items-center gap-2">
        <span className="mr-auto text-xs text-muted-foreground">{stepLabel(p.view)}</span>
        {p.onBack ? (
          <Button variant="ghost" size="sm" onClick={p.onBack}>
            Back
          </Button>
        ) : null}
        <Button variant="ghost" size="sm" onClick={p.onEnd}>
          End tour
        </Button>
        <Button variant="primary" size="sm" onClick={p.onNext}>
          Next
        </Button>
      </div>
    </>
  );
}

const CARD = "mb-2 rounded-2xl border border-border bg-card p-3 glass:border-transparent glass:glass-chrome";

export default function GuideStepCard() {
  const g = useGuide();
  if (isActive(g)) {
    const view = guideView();
    if (!view) return null;
    return (
      <section data-guide-card="" aria-label="Guide" className={CARD}>
        <GuideStepCardBody
          view={view}
          onBack={g.step > 0 ? backStep : undefined}
          onNext={nextStep}
          onEnd={() => endGuide("end")}
        />
      </section>
    );
  }
  if (g.offer) {
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
  return null;
}
