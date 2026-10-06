"use client";

/**
 * WS-45 S4 (D90): does the platform pick the tier of *agentName*?
 *
 * For a surface outside the composer that forces a model from a `chat_model`
 * setting: the email chat, the Tasks and Projects rails, and the two settings
 * controls. `lib/tierRouting.ts` holds the rules (`tierRoutedFor`,
 * `readsChatModel`). This hook only reads the agent list that they need.
 *
 * - **UI flag off:** `{ known: true, covered: false }` at once, and NO
 *   request. So with `NEXT_PUBLIC_AI_TIER_ROUTING` off, a surface that calls
 *   this does what it did on `origin/main`, with the same requests.
 * - **UI flag on:** one `GET /api/agent/list`. `known` is false until it lands.
 *   A fault reads as known and not covered, so a surface keeps today's model
 *   (fails closed).
 *
 * The gateway stamps `tier_routed` on each entry from
 * `acb_skills.tier_policy.tier_routing_on`, the one reader of the backend
 * flag. No file in the Control Plane names a covered agent.
 */
import { useEffect, useState } from "react";

import {
  tierRoutedFor,
  tierRoutingUiOn,
  type TierCoverage,
  type TierRoutedEntry,
} from "@/lib/tierRouting";

export function useTierRouted(agentName: string): TierCoverage {
  const uiOn = tierRoutingUiOn();
  const [entries, setEntries] = useState<TierRoutedEntry[] | null>(null);
  useEffect(() => {
    if (!uiOn) return;
    let cancelled = false;
    fetch("/api/agent/list")
      .then((r) => r.json())
      .then((data: unknown) => {
        if (!cancelled) setEntries(Array.isArray(data) ? (data as TierRoutedEntry[]) : []);
      })
      .catch(() => {
        if (!cancelled) setEntries([]);
      });
    return () => {
      cancelled = true;
    };
  }, [uiOn]);
  const known = !uiOn || entries !== null;
  return {
    known,
    covered: tierRoutedFor({
      uiOn,
      agentsKnown: entries !== null,
      entry: entries?.find((a) => a.name === agentName),
    }),
  };
}
