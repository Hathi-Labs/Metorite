/**
 * The choices of the time-zone select in the Calendar settings (WS-66 RX-7).
 *
 * The list is every zone the browser knows (`Intl.supportedValuesOf`), with
 * "UTC" first and the member's saved zone always present, so a saved zone the
 * browser does not list still shows as the value. Each option carries its
 * offset as a hint ("UTC+05:30"), and the zone name as keywords, so the
 * filter finds "Kolkata" and "India".
 *
 * Fence: `zoneChoices.test.ts`.
 */
import { offsetMinutes, offsetText } from "@/lib/reminderClock";
import { knownZone } from "@/lib/shell/reminderOpen";
import type { SelectOption } from "@/components/ui/SelectButton";

/** A short list for a browser with no `Intl.supportedValuesOf`. */
const FALLBACK_ZONES = [
  "Pacific/Honolulu",
  "America/Anchorage",
  "America/Los_Angeles",
  "America/Denver",
  "America/Chicago",
  "America/New_York",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Africa/Cairo",
  "Africa/Johannesburg",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Dhaka",
  "Asia/Bangkok",
  "Asia/Singapore",
  "Asia/Shanghai",
  "Asia/Tokyo",
  "Australia/Sydney",
  "Pacific/Auckland",
];

function listedZones(): string[] {
  try {
    const list = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone");
    if (list && list.length > 0) return list;
  } catch {
    // An old browser: use the short list.
  }
  return FALLBACK_ZONES;
}

/** "UTC+05:30" for a zone at an instant, or "" when the zone is not known. */
export function zoneHint(zone: string, nowMs: number = Date.now()): string {
  if (!knownZone(zone)) return "";
  return `UTC${offsetText(offsetMinutes(nowMs, zone))}`;
}

/** The label of a zone: its name, with the underscores as spaces ("America/New York"). */
export function zoneLabel(zone: string): string {
  return zone.replace(/_/g, " ");
}

/**
 * The options of the select. `saved` is the value now: it is listed even when
 * the browser does not know it. "UTC" is first, then the zones in the
 * browser's order.
 */
export function zoneOptions(saved: string | null | undefined, nowMs: number = Date.now()): SelectOption[] {
  const names = new Set<string>(["UTC"]);
  for (const z of listedZones()) names.add(z);
  const current = (saved ?? "").trim();
  if (current) names.add(current);
  return [...names]
    .filter((z) => z === current || z === "UTC" || knownZone(z))
    .map((z) => ({ value: z, label: zoneLabel(z), hint: zoneHint(z, nowMs), keywords: z }));
}
