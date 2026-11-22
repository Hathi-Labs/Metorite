"use client";

/**
 * The one-line pin card of My Day (`package_host.md` §9.3 item 2, PKG-19a-2).
 * It sits under the Do first band, behind `NEXT_PUBLIC_PIN_SUGGEST`.
 * "You opened Field visits 9 times this week. Pin it?" with Yes and No.
 *
 * It draws NOTHING when no suggestion stands. Yes pins the pane
 * (`pinSuggestAccept.ts`, the only writer). No hides the suggestion for 30
 * days, in this browser (`pinSuggestStore.ts`).
 */
import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import type { NavSection } from "@/lib/nav";

import { suggestPin, type PinSuggestion } from "./pinSuggest";
import { acceptPin, type PinAcceptInput } from "./pinSuggestAccept";
import { answerCard } from "./pinSuggestCard";
import { hideSuggestion, readHidden, readOpens } from "./pinSuggestStore";
import { starredHrefs, starrableHeld } from "./presets";

/** The card as drawn. No hook, so a test can read its tree. */
export function PinSuggestCardView({
  suggestion,
  onYes,
  onNo,
  className = "",
}: {
  suggestion: PinSuggestion | null;
  onYes: () => void;
  onNo: () => void;
  className?: string;
}) {
  if (!suggestion) return null;
  return (
    <div
      role="status"
      data-testid="pin-suggest-card"
      className={`flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-border bg-card px-4 py-2.5 ${className}`}
    >
      <p className="flex min-w-0 flex-1 items-start gap-1.5 text-sm text-foreground">
        <Icon name="Lightbulb" size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 break-words" data-testid="pin-suggest-why">
          {suggestion.why}
        </span>
      </p>
      <div className="flex shrink-0 gap-2">
        <Button size="sm" onClick={onYes} data-testid="pin-suggest-yes">
          Yes
        </Button>
        <Button size="sm" variant="ghost" onClick={onNo} data-testid="pin-suggest-no">
          No
        </Button>
      </div>
    </div>
  );
}

export default function PinSuggestCard({
  email,
  accept,
  sections,
  layout,
  className = "",
}: {
  email: string | null;
  accept: Omit<PinAcceptInput, "sections" | "layout"> | null;
  sections: readonly NavSection[];
  layout: PinAcceptInput["layout"];
  className?: string;
}) {
  const toast = useToast();
  const [suggestion, setSuggestion] = useState<PinSuggestion | null>(null);
  // The browser store is read after hydration, so the server render and the
  // first client render both draw nothing.
  useEffect(() => {
    if (!email || !accept) return;
    const now = Date.now();
    const held = starrableHeld(sections);
    const starred = starredHrefs(layout, sections) ?? held.map((p) => p.href);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read from the browser store, after hydration
    setSuggestion(
      suggestPin({ opens: readOpens(email), starred, hidden: readHidden(email, now), panes: held, now }),
    );
  }, [email, accept, sections, layout]);

  const answer = (a: "yes" | "no") => {
    if (!suggestion) return;
    void answerCard(a, suggestion.href, {
      accept: (href) =>
        acceptPin(href, { ...accept!, sections, layout }).catch(() =>
          toast.show({ key: "pin-suggest", variant: "error", title: "Your sidebar was not saved. Try again." }),
        ),
      hide: (href) => hideSuggestion(email, href, Date.now()),
      close: () => setSuggestion(null),
    });
  };
  return <PinSuggestCardView suggestion={email && accept ? suggestion : null} onYes={() => answer("yes")} onNo={() => answer("no")} className={className} />;
}
