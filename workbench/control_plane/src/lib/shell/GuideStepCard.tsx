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
 *
 * The tour's last stop (GD-5a) offers one chip per live app that the member
 * holds (`tourChips`). A chip ends the tour and opens its app. The phone's
 * peek strip (`AssistantDock`) stays one line, so it shows no chip.
 */

import { useRouter } from "next/navigation";

import { useAccess } from "@/components/AccessProvider";
import Button from "@/components/ui/Button";
import { useViewMode } from "@/components/ViewModeProvider";
import { visibleSections } from "@/lib/nav";

import { closeAssistant, onChatLink, setAssistantMode } from "./assistant";
import { chatFirstOn } from "./chatFlag";
import {
  ANCHOR_MISSING_NOTE,
  TOUR_GUIDE_ID,
  acceptGuideOffer,
  backStep,
  dismissGuideOffer,
  endGuide,
  guideView,
  isActive,
  nextLabel,
  nextStep,
  tourChips,
  useGuide,
  type GuideView,
  type TourChip,
} from "./guide";
import { stepLabel } from "./GuideSpotlight";
import { starrableHeld } from "./presets";

/** The card's content. Exported so a test reads its markup. */
export function GuideStepCardBody(p: {
  view: Pick<GuideView, "say" | "index" | "total" | "missing"> & { isLast?: boolean };
  onBack?: () => void;
  onNext: () => void;
  onEnd: () => void;
  /** The app chips of the tour's last stop (GD-5a). None on any other stop. */
  chips?: readonly TourChip[];
  onChip?: (chip: TourChip) => void;
}) {
  const chips = p.chips ?? [];
  return (
    <>
      <p className="text-sm text-foreground">{p.view.say}</p>
      {p.view.missing ? <p className="mt-1 text-xs text-muted-foreground">{ANCHOR_MISSING_NOTE}</p> : null}
      {chips.length > 0 ? (
        <div data-guide-chips="" role="group" aria-label="Open an app" className="mt-2 flex flex-wrap gap-1.5">
          {chips.map((c) => (
            <Button key={c.href} variant="secondary" size="sm" icon={c.icon} onClick={() => p.onChip?.(c)}>
              {c.label}
            </Button>
          ))}
        </div>
      ) : null}
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
          {nextLabel(p.view.isLast)}
        </Button>
      </div>
    </>
  );
}

const CARD = "mb-2 rounded-2xl border border-border bg-card p-3 glass:border-transparent glass:glass-chrome";

export default function GuideStepCard() {
  const g = useGuide();
  const router = useRouter();
  const { access, loading } = useAccess();
  const { isMobile } = useViewMode();
  if (isActive(g)) {
    const view = guideView();
    if (!view) return null;
    // The live apps the member holds, by the sidebar's own filter, with the
    // preview flag off: a guide never points at a preview pane (§1).
    const chips =
      view.guide.id === TOUR_GUIDE_ID && view.isLast
        ? tourChips(starrableHeld(visibleSections(loading ? null : access.features, access.is_admin, false)))
        : [];
    return (
      <section data-guide-card="" aria-label="Guide" className={CARD}>
        <GuideStepCardBody
          view={view}
          onBack={g.step > 0 ? backStep : undefined}
          onNext={nextStep}
          onEnd={() => endGuide("end")}
          chips={chips}
          onChip={(c) => {
            // GD-10: start the app's chapter. Until then the chip ends the tour and opens the app.
            endGuide("done");
            // On a phone the sheet gets out of the way, as for a tapped chat link.
            const act = onChatLink({ phone: isMobile, chatFirst: chatFirstOn() });
            if (act === "peek") setAssistantMode("peek");
            else if (act === "close") closeAssistant();
            router.push(c.href);
          }}
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
