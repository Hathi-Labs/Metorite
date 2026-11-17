"use client";

/**
 * The two hooks of the Connect UI (WS-CAL CE-10c, CE-10d): the list read through the cache, and
 * the Connect press. They live apart from the components, so `AlsoSyncCalendar` and
 * `CalendarConnections` share them with no import cycle. `CalendarConnections.tsx` re-exports both.
 */

import { useRef, useState } from "react";

import { useCachedResource } from "@/lib/useCachedResource";

import {
  CONNECTIONS_KEY,
  getConnections,
  rememberStarted,
  startConnect,
  type ConnectionsList,
  type Provider,
} from "./calendarConnections";
import { safeConsentUrl } from "./calendarIds";

/** The list, read through the cache. `null` data means "not enabled". */
export function useCalendarConnections() {
  return useCachedResource<ConnectionsList | null>(CONNECTIONS_KEY, () => getConnections());
}

/**
 * The Connect press: ask for the consent URL, then send the browser there. A `loginHint` is the
 * address of a connected mailbox (CE-10d).
 */
export function useConnect() {
  const [connecting, setConnecting] = useState<Provider | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  async function connect(p: Provider, loginHint?: string) {
    // One press at a time: a double click sends one POST and assigns the location once.
    if (inFlight.current) return;
    inFlight.current = true;
    setConnecting(p);
    setError(null);
    const r = await startConnect(p, fetch, loginHint);
    // The server checks the host too. The client checks the same allowlist before it leaves.
    const url = r.kind === "ok" ? safeConsentUrl(p, r.url) : null;
    if (url) {
      rememberStarted(p);
      window.location.assign(url);
      return;
    }
    setError(r.kind === "error" ? r.message : "Connecting did not work. Try again.");
    setConnecting(null);
    inFlight.current = false;
  }
  return { connecting, error, connect };
}
