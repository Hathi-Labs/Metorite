"use client";

/**
 * The range of the first import, the step before the sign-in (WS-17 EM-T6d
 * item 2, D-EM-10).
 *
 * `ConnectChoices` draws it in place of the provider list, so the empty state
 * and the add-account dialog show the same step. It holds no state. The
 * caller owns the chosen range, and "Continue" starts the sign-in.
 *
 * The choices are a radio group of `Button`s, the worked pattern of
 * `projects/components/SpaceSettings.tsx`: `selected` draws the state, and
 * `role="radio"` with `aria-checked` names it.
 */

import { useId } from "react";
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import {
  CONNECT_PROVIDERS,
  IMPORT_RANGE_CHOICES,
  IMPORT_RANGE_COPY,
  type ConnectProviderId,
} from "../lib/connect";

export function ImportRangeStep({
  provider,
  months,
  onChange,
  onBack,
  onContinue,
}: {
  provider: ConnectProviderId;
  months: number;
  onChange: (months: number) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const p = CONNECT_PROVIDERS.find((c) => c.id === provider);
  const titleId = useId();
  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon name={p?.icon ?? "Mail"} size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="text-sm font-medium text-foreground">
            {IMPORT_RANGE_COPY.title}
          </h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{IMPORT_RANGE_COPY.body}</p>
        </div>
      </div>

      {/* "Only new mail" takes the first row, then two rows of three. */}
      <div role="radiogroup" aria-labelledby={titleId} className="grid grid-cols-3 gap-1.5">
        {IMPORT_RANGE_CHOICES.map((c) => (
          <Button
            key={c.months}
            variant="secondary"
            size="sm"
            role="radio"
            aria-checked={c.months === months}
            selected={c.months === months}
            onClick={() => onChange(c.months)}
            className={c.months === 0 ? "col-span-3" : ""}
          >
            {c.label}
          </Button>
        ))}
      </div>

      <div className="flex items-center justify-between gap-2 pt-1">
        <Button variant="ghost" size="sm" icon="ArrowLeft" onClick={onBack}>
          {IMPORT_RANGE_COPY.back}
        </Button>
        <Button variant="primary" size="sm" onClick={onContinue}>
          {IMPORT_RANGE_COPY.continueTo(provider)}
        </Button>
      </div>
    </section>
  );
}
