"use client";

/**
 * The disclosure, the last step before the browser leaves Metorite (WS-17
 * EM-G11a, spec §12.3.13.4). Google asks for it: the words must sit in the app
 * and must come right before the consent screen.
 *
 * Two places draw it. `ConnectChoices` draws it after the range step, so the
 * empty state, the add-account dialog and the "Try again" of the callback page
 * all reach it. The reconnect banner of `page.tsx` draws it in a `Modal`.
 *
 * It holds one state, the checkbox. Only a click with the box ticked calls
 * `onAcknowledge`, and only this file hands out `DISCLOSURE_VERSION`. So no
 * caller can start a sign-in without the member's tick. The gateway holds the
 * other half: it refuses a Gmail connect without the current version.
 * Fence: `connect.test.ts`, `every-handleConnect-caller-passes-the-disclosure`.
 *
 * Focus: the step is long, so focus goes to its title when it opens, and a
 * keyboard reads down from there. A focus on the box would scroll past the
 * words. After "Back", `ImportRangeStep` takes focus to the checked range.
 *
 * Outlook uses the same step with the Microsoft words and the shared `Button`.
 * The gateway does not require the acknowledgement for Microsoft (item 4).
 */

import { useEffect, useId, useRef, useState } from "react";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import {
  DISCLOSURE_COPY,
  DISCLOSURE_VERSION,
  PRIVACY_POLICY_URL,
  type ConnectProviderId,
} from "../lib/connect";
import { GoogleButton } from "./GoogleButton";
import { ProviderGlyph } from "./ProviderGlyph";

export function DisclosureStep({
  provider,
  onBack,
  onAcknowledge,
  backLabel = DISCLOSURE_COPY.back,
}: {
  provider: ConnectProviderId;
  onBack: () => void;
  /** The member ticked the box and left for the provider. */
  onAcknowledge: (disclosure: string) => void;
  /** The words of the way out. A dialog with no step behind it says "Cancel". */
  backLabel?: string;
}) {
  const words = DISCLOSURE_COPY[provider];
  const [agreed, setAgreed] = useState(false);
  const titleId = useId();
  const boxId = useId();
  const titleRef = useRef<HTMLHeadingElement>(null);

  // Mount only.
  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  const go = () => {
    if (agreed) onAcknowledge(DISCLOSURE_VERSION);
  };

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <ProviderGlyph provider={provider} />
        <div className="min-w-0 flex-1">
          <h3
            id={titleId}
            ref={titleRef}
            tabIndex={-1}
            className="rounded-sm text-sm font-medium text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {words.title}
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{words.lead}</p>
        </div>
      </div>

      <div className="flex flex-col gap-2.5">
        {words.sections.map((s) => (
          <div key={s.heading}>
            <h4 className="text-xs font-medium text-foreground">{s.heading}</h4>
            <p className="mt-0.5 text-xs text-muted-foreground">{s.body}</p>
          </div>
        ))}
      </div>

      <a
        href={PRIVACY_POLICY_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="cc-link self-start rounded-sm text-xs text-primary underline underline-offset-2"
      >
        {DISCLOSURE_COPY.privacy}
      </a>

      <label htmlFor={boxId} className="flex cursor-pointer items-start gap-2 text-xs text-foreground">
        <Checkbox
          id={boxId}
          className="mt-px"
          checked={agreed}
          onChange={(e) => setAgreed(e.target.checked)}
        />
        <span>{words.agree}</span>
      </label>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <Button variant="ghost" size="sm" icon="ArrowLeft" onClick={onBack}>
          {backLabel}
        </Button>
        {provider === "gmail" ? (
          <GoogleButton disabled={!agreed} onClick={go} />
        ) : (
          <Button variant="primary" size="sm" disabled={!agreed} onClick={go}>
            {words.button}
          </Button>
        )}
      </div>
    </section>
  );
}
