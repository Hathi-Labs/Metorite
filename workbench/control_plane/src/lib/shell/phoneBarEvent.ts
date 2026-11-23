"use client";

/**
 * The one event from a page to the phone bar (MOB-12, task 2005).
 *
 * A page calls `usePhoneBar` with what it shows now. The shell keeps ONE
 * listener for `cc-phone-bar` and draws the bar from the registry
 * (`phoneBar.ts`). `null` removes the report, and the hook sends it when the
 * page unmounts.
 *
 * The last report stays in this module too. A page effect runs before the
 * effect of the shell above it on the first paint, so the shell reads the
 * stored report when it mounts and does not miss it.
 */

import { useEffect, useState } from "react";

export const PHONE_BAR_EVENT = "cc-phone-bar";

export interface PhoneBarReport {
  /** The id of the selected place tab, or none. */
  active?: string | null;
  /** The ids of the tabs that have nothing to do now. */
  hide?: string[];
  /** The count to draw on a tab, by id. A zero draws nothing. */
  counts?: Record<string, number>;
}

let current: PhoneBarReport | null = null;

/** The report the page sent last, or null. */
export function currentPhoneBarReport(): PhoneBarReport | null {
  return current;
}

/** Send a report. `null` clears it. */
export function sendPhoneBar(report: PhoneBarReport | null): void {
  current = report;
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(PHONE_BAR_EVENT, { detail: report }));
}

/** Report what the page shows. Sends `null` when the page unmounts. */
export function usePhoneBar(report: PhoneBarReport): void {
  const key = JSON.stringify([
    report.active ?? null,
    report.hide ?? [],
    Object.entries(report.counts ?? {}).sort(),
  ]);
  useEffect(() => {
    sendPhoneBar({
      active: report.active ?? null,
      hide: report.hide ?? [],
      counts: report.counts ?? {},
    });
    // The key holds every field of the report.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  useEffect(() => () => sendPhoneBar(null), []);
}

/**
 * The tab whose tap opened the sheet that shows now (a place tab that opens a
 * drawer sheet shows selected while it is open). `markTab` names the tab as
 * the page opens the sheet. The id clears when the drawer closes, or when it
 * shows the Menu.
 */
export function useSheetTab(isOpen: boolean, shows: string): [string | null, (id: string) => void] {
  const [tab, setTab] = useState<string | null>(null);
  if (!isOpen && tab !== null) setTab(null);
  return [isOpen && shows === "sheet" ? tab : null, setTab];
}
