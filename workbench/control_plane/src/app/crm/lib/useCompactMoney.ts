/**
 * The CRM's short money in the member's number system (owner, 2026-10-19).
 *
 * Spec: `project-docs/specs/whatsapp_assistant_channel.md` §13.10.
 *
 * `compactMoney` (`./format.ts`) stays pure. This hook gives it the member's
 * resolved system from `useNumberSystem`, which reads
 * `GET /api/settings/number-system` ONCE a page and shares the answer with
 * every caller, so a board of forty lanes makes one request, not forty.
 * Until the answer arrives the system is "indian", the default of every org.
 *
 * Every CRM screen that writes a short amount calls this hook, never
 * `compactMoney` itself. Fence: `./useCompactMoney.test.ts`.
 */
import { useNumberSystem } from "@/lib/numberSystem";

import { compactMoney } from "./format";

export type CompactMoney = (amount: number | null | undefined, currency?: string) => string;

export function useCompactMoney(): CompactMoney {
  const system = useNumberSystem() ?? "indian";
  return (amount, currency = "INR") => compactMoney(amount, currency, system);
}
