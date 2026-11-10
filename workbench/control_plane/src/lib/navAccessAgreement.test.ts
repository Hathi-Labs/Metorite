/**
 * The two route→feature maps must agree.
 *
 * Written 2026-08-24 with **D54** (board WS-39 S2) because adding the Calendar
 * pane nearly shipped the bug this fences.
 *
 * **The shape of the hazard.** A pane's gate is declared twice, in two files,
 * for two different consumers:
 *
 *   - `nav.ts` — `NavPane.feature`, which decides whether the pane is RENDERED.
 *   - `access.ts` — `HREF_FEATURES`, which `canSeePath`/`featureForPath` use to
 *     decide whether the ROUTE may be reached.
 *
 * Only the second is a control. `featureForPath` returns `null` when nothing
 * matches, and **null means unguarded** — so a pane added to `nav.ts` alone is
 * hidden from the sidebar of members who lack the grant while remaining
 * reachable by URL to everyone. The sidebar looks right, which is what makes it
 * survive review.
 *
 * This is a second-implementation smell of the kind CLAUDE.md §5 names, and the
 * honest fix is one map. That is not this slice's change — collapsing them
 * touches every pane and both consumers. Until then, **agreement is fenced**:
 * the duplication is allowed to exist but not allowed to drift.
 *
 * Structural: it walks every pane in the registry, so it fails for a pane
 * nobody thought to name here.
 */
import { describe, expect, it } from "vitest";

import { featureForPath, isAlwaysAllowed } from "./access";
import { PANES } from "./nav";

describe("nav.ts and access.ts agree about what gates each route", () => {
  it("every gated pane is gated by the SAME feature in both maps", () => {
    const disagreements: string[] = [];
    for (const pane of PANES) {
      if (!pane.feature) continue;
      const viaAccess = featureForPath(pane.href);
      if (viaAccess !== pane.feature) {
        disagreements.push(
          `${pane.href}: nav says "${pane.feature}", ` +
            `access says ${viaAccess === null ? "UNGATED" : `"${viaAccess}"`}`,
        );
      }
    }
    expect(
      disagreements,
      "A pane gated in nav.ts but not in access.ts is REACHABLE BY URL to " +
        "everyone — nav only hides it. Add the route to HREF_FEATURES in " +
        "access.ts (longest-prefix match, so order does not matter).",
    ).toEqual([]);
  });

  it("every ungated pane is genuinely ungated on the access side too", () => {
    // The inverse drift: a pane offered to everyone in the sidebar while
    // `canSeePath` refuses it produces a link that leads to "no access" — the
    // failure `/access` exists to explain, arriving from the nav itself.
    const disagreements: string[] = [];
    for (const pane of PANES) {
      if (pane.feature || pane.adminOnly) continue;
      const viaAccess = featureForPath(pane.href);
      if (viaAccess !== null) {
        disagreements.push(`${pane.href}: nav says ungated, access says "${viaAccess}"`);
      }
    }
    expect(disagreements).toEqual([]);
  });

  it("pins the Calendar pane specifically (D54)", () => {
    // The worked example, kept explicit because the reasoning is the part that
    // travels: Calendar rides `feature:tasks` rather than a slug of its own,
    // because a new slug is a grant nobody holds and would ship the app dark
    // to every existing member (`calendar_focus_os.md` §10.2).
    const calendar = PANES.find((p) => p.href === "/calendar");
    expect(calendar, "the Calendar pane is missing from nav.ts").toBeTruthy();
    expect(calendar!.feature).toBe("tasks");
    expect(calendar!.launch).toBe("live");
    expect(featureForPath("/calendar")).toBe("tasks");
    expect(isAlwaysAllowed("/calendar")).toBe(false);
  });

  it("pins the Integrations pane: ungated, by an EXACT entry (D-EA-9, WS-55 EA-4b)", () => {
    // Every member opens the app for the AI agents view, and the page gates
    // each other view on `feature:integrations`. The ALWAYS_ALLOWED entry is
    // exact, so a later sub-route keeps the gate of HREF_FEATURES.
    // Mutation, seen red: drop "/integrations" from ALWAYS_ALLOWED, or give
    // the pane its feature back.
    const pane = PANES.find((p) => p.href === "/integrations");
    expect(pane, "the Integrations pane is missing from nav.ts").toBeTruthy();
    expect(pane!.feature).toBeUndefined();
    expect(pane!.adminOnly).toBeFalsy();
    expect(pane!.team).toBe("admin");
    expect(pane!.launch).toBe("live");
    expect(isAlwaysAllowed("/integrations")).toBe(true);
    expect(featureForPath("/integrations")).toBeNull();
    expect(featureForPath("/integrations/anything")).toBe("integrations");
    expect(isAlwaysAllowed("/integrations/anything")).toBe(false);
  });
});
