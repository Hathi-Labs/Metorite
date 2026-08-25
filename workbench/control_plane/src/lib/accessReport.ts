/**
 * "Why can't I see that pane?" — the answer, as data.
 *
 * A hidden nav item is the platform's least debuggable failure. Four different
 * causes render as exactly the same nothing:
 *
 *   1. your roles do not grant the feature;
 *   2. a deny override cancels a grant you appear to hold;
 *   3. the slug is registered but the pane has no nav entry;
 *   4. you are signed out, or the gateway is unreachable, so access resolved
 *      to NO_ACCESS.
 *
 * Every one of them looks like "the app doesn't have that". This module turns
 * the server's own answer into a sentence per pane, so the next person who says
 * "I don't have access to this" can read the reason off the screen instead of
 * someone querying the database to find out.
 *
 * Nothing here decides anything. `Access` is the gateway's resolved answer, and
 * the gateway's `require_permission()` is the real boundary — this is a
 * renderer for a decision already made.
 */

import type { Access } from "./access";
import { NAV_SECTIONS, previewAppsVisible, type LaunchStatus } from "./nav";

/**
 * Why a pane is, or is not, on this viewer's sidebar.
 *
 * `not-launched` is the fifth cause, added with D49 (`launch_surface.md` LS-3),
 * and it is the one that most needs naming: a `preview` pane is missing for a
 * reason NO grant can fix, so reporting it as `denied` would send a customer
 * admin hunting for a permission that would change nothing.
 */
export type PaneStatus = "granted" | "denied" | "signed-out" | "not-launched";

export interface PaneReport {
  href: string;
  label: string;
  /** The feature slug guarding it, or null for an ungated pane. */
  feature: string | null;
  status: PaneStatus;
  /** The permission needed, when it is missing. */
  permission?: string;
  /** One sentence a person can act on. */
  reason: string;
}

/**
 * Every pane the navigation can show, flattened, in sidebar order.
 *
 * Deliberately EVERY pane, `preview` ones included. This report's whole job is
 * to answer "is it hidden, or does it not exist" — dropping the unlaunched
 * panes here would make them indistinguishable from the second case, which is
 * the confusion the module exists to remove.
 */
export function allPanes(): Array<{
  href: string;
  label: string;
  feature: string | null;
  launch: LaunchStatus;
}> {
  return NAV_SECTIONS.flatMap((section) =>
    section.items.map((item) => ({
      href: item.href,
      label: item.label,
      feature: item.feature ?? null,
      launch: item.launch,
    })),
  );
}

/**
 * A per-pane verdict for this viewer.
 *
 * Deliberately reports on **every** pane, not only the hidden ones: a list that
 * shows just the failures cannot answer "is it hidden, or does it not exist" —
 * the question somebody actually has when a pane they were told about is
 * absent.
 */
export function paneReport(access: Access): PaneReport[] {
  const granted = new Set(access.features);
  const deniedBy = new Map(
    access.features_denied.map((d) => [d.slug, d.permission]),
  );

  const previewVisible = previewAppsVisible();

  return allPanes().map(({ href, label, feature, launch }) => {
    if (!access.authenticated) {
      return {
        href,
        label,
        feature,
        status: "signed-out" as const,
        reason: "Not signed in — nothing is resolved yet.",
      };
    }
    // Launch status is checked BEFORE the grant, because it is the stronger
    // answer: no permission reveals a pane we are not offering yet (§3.4).
    if (launch === "preview" && !previewVisible) {
      return {
        href,
        label,
        feature,
        status: "not-launched" as const,
        reason:
          "Not available yet — this app is still being built, so it is not in " +
          "the menu. A grant will not reveal it.",
      };
    }
    if (!feature) {
      return {
        href,
        label,
        feature,
        status: "granted" as const,
        reason: "Open to every signed-in member.",
      };
    }
    if (granted.has(feature)) {
      return {
        href,
        label,
        feature,
        status: "granted" as const,
        reason: `Granted by \`feature:${feature}\`.`,
      };
    }
    const permission = deniedBy.get(feature) ?? `feature:${feature}`;
    // A deny override is worth calling out by name: it is the one case where
    // adding the grant changes nothing, so "ask an admin to grant it" would be
    // advice that cannot work.
    const explicitlyDenied = access.denied.some(
      (d) => d === permission || d === "feature:*" || d === "*",
    );
    return {
      href,
      label,
      feature,
      status: "denied" as const,
      permission,
      reason: explicitlyDenied
        ? `Blocked by a deny override on \`${permission}\` — removing that is the only fix; adding the grant will not help.`
        : `Needs \`${permission}\`, which your roles (${access.roles.join(", ") || "none"}) do not grant.`,
    };
  });
}

/** The short headline above the list. */
export function summarise(report: PaneReport[]): string {
  if (report.some((r) => r.status === "signed-out")) {
    return "Signed out — no access is resolved.";
  }
  // Unlaunched panes are counted separately from denied ones and excluded from
  // the denominator: "3 of 24 panes available" would read as a broken account
  // when sixteen of those 24 are simply not being offered to anybody yet.
  const unlaunched = report.filter((r) => r.status === "not-launched").length;
  const offered = report.length - unlaunched;
  const denied = report.filter((r) => r.status === "denied").length;
  const tail = unlaunched > 0 ? ` · ${unlaunched} not available yet` : "";
  if (denied === 0) return `All ${offered} available apps are open to you${tail || "."}`;
  return `${offered - denied} of ${offered} apps available · ${denied} need a grant${tail}`;
}

/**
 * Slugs the SERVER knows about but the sidebar has no entry for.
 *
 * The fourth failure mode, and the only one invisible from the pane list: a
 * feature registered in `feature_catalog` and granted to you, that no nav item
 * points at. It is reachable by URL and undiscoverable in the UI — which reads,
 * again, as "the app doesn't have that".
 */
export function unmappedFeatures(access: Access): string[] {
  // `allPanes()` includes preview panes, which is what we want here: a slug
  // whose pane exists but is not launched is MAPPED — it has a home to return
  // to — and reporting it as unmapped would send someone looking for a nav
  // entry to write that already exists.
  const navSlugs = new Set(
    allPanes()
      .map((p) => p.feature)
      .filter((f): f is string => Boolean(f)),
  );
  return access.features.filter((f) => !navSlugs.has(f)).sort();
}
