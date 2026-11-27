"use client";

/**
 * "Also sync its calendar?" (WS-CAL CE-10d, task 3069).
 *
 * A member whose Gmail or Outlook mailbox is already connected can add its calendar with one
 * press. Mail and calendar stay two connections, and the member consents once for the calendar
 * scope. The gateway lists the offers in `mailbox_offers`. ONE component draws them in three
 * places: Calendar settings, the Calendar page prompt and the email Accounts list.
 *
 * It draws nothing while loading, while the feature is off (a 404 on the list), and while the
 * list holds no offer. The press posts the connect call with the address as `login_hint`, then
 * sends the browser to the consent URL, and only through `safeConsentUrl`. The return from the
 * provider lands on Calendar, where `CalendarConnectReturn` shows the result and refreshes the
 * list. The offer then leaves, because the gateway no longer lists it.
 *
 * Fence: `AlsoSyncCalendar.test.tsx`.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";

import {
  FULL_GUIDE_LINE,
} from "../lib/connectGuide";
import {
  OFFER_BUTTON,
  PROVIDER_NAME,
  offerButtonLabel,
  offerLine,
  visibleOffers,
  type ConnectionsList,
  type MailboxOffer,
} from "../lib/calendarConnections";
import { useCalendarConnections, useConnect } from "../lib/useCalendarConnect";

export interface AlsoSyncViewProps {
  list: ConnectionsList | null | undefined;
  /** The address whose press is running, or null. */
  activeAddress: string | null;
  /** A request runs. Every button waits. */
  busy: boolean;
  error: string | null;
  onSync: (offer: MailboxOffer) => void;
  className?: string;
}

/** The pure face. Null when there is nothing to offer. */
export function AlsoSyncView({ list, activeAddress, busy, error, onSync, className = "" }: AlsoSyncViewProps) {
  const offers = visibleOffers(list);
  if (offers.length === 0) return null;
  return (
    <section aria-label="Also sync its calendar" className={className}>
      <ul className="space-y-1.5">
        {offers.map((o) => (
          <li
            key={`${o.provider}:${o.address}`}
            className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-background/60 p-2 text-[11px]"
          >
            <span className="min-w-0 flex-1 basis-40 break-words text-muted-foreground">
              <span className="font-medium text-foreground">{PROVIDER_NAME[o.provider]}.</span> {offerLine(o)}
            </span>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              icon="Calendar"
              aria-label={offerButtonLabel(o)}
              loading={busy && activeAddress === o.address}
              disabled={busy}
              onClick={() => onSync(o)}
            >
              {OFFER_BUTTON}
            </Button>
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="mt-1.5 text-[11px] text-destructive">
          {error} <span className="text-muted-foreground">{FULL_GUIDE_LINE}</span>
        </p>
      )}
    </section>
  );
}

/** The wired component. It reads the cached list and runs the connect call. */
export default function AlsoSyncCalendar({ className = "" }: { className?: string }) {
  const res = useCalendarConnections();
  const { connecting, error, connect } = useConnect();
  const [active, setActive] = useState<string | null>(null);
  return (
    <AlsoSyncView
      list={res.data}
      activeAddress={active}
      busy={connecting !== null}
      error={error}
      className={className}
      onSync={(o) => {
        setActive(o.address);
        void connect(o.provider, o.address);
      }}
    />
  );
}
