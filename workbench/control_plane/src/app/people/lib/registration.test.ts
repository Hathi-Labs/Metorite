/**
 * People · the app is registered in every place that must know about it.
 *
 * Spec: `project-docs/specs/people_center_app.md` §6 · ticket WS-28b.
 *
 * The failure this exists to catch is the one migration 140 shipped for real:
 * a surface seeded in one place and missing from another is **unreachable,
 * silently, for everybody including the owner** — and every other test still
 * passes, because nothing else reads the registry that was skipped.
 *
 * The Python half (`FEATURES` ↔ `feature_catalog`, and the router's own gate)
 * is fenced in `tests/unit/test_org_access_control.py` and
 * `tests/unit/test_people_directory.py`. This is the frontend half.
 */
import { describe, expect, it } from "vitest";

import { CENTERS } from "@/lib/centers";
import { NAV_SECTIONS } from "@/lib/nav";
import { featureForPath } from "@/lib/access";

const PEOPLE_HREF = "/people";
const PEOPLE_FEATURE = "people";

describe("the People app is reachable", () => {
  it("has a nav pane gated on its own feature", () => {
    const items = NAV_SECTIONS.flatMap((s) => s.items);
    const pane = items.find((i) => i.href === PEOPLE_HREF);

    expect(pane, "/people has no nav entry — the pane would never render").toBeTruthy();
    expect(pane?.feature).toBe(PEOPLE_FEATURE);
  });

  it("maps its route to the same feature slug", () => {
    // A pane gated on one slug and a route guarded by another is how a member
    // sees a link that then refuses them.
    expect(featureForPath(PEOPLE_HREF)).toBe(PEOPLE_FEATURE);
  });

  it("puts MY profile in the Personal Center, ungated", () => {
    // WS-28g-2 / D-PC-15. `feature:people` gates the DIRECTORY and is
    // `is_default false`; a person's own record is not the directory, and
    // gating it hid the surface from everyone it was built for.
    const items = NAV_SECTIONS.flatMap((s) => s.items);
    const mine = items.find((i) => i.href === "/people/me");

    expect(mine, "/people/me has no nav entry — nobody would find it").toBeTruthy();
    expect(mine?.feature, "my own profile must not need a feature grant")
      .toBeUndefined();
  });

  it("keeps it out of the directory's gate on the client too", () => {
    // The gateway serves it from a router with no feature dependency; this is
    // the same answer client-side, and it needs the explicit rule because
    // `featureForPath` matches by PREFIX and would otherwise inherit `people`.
    expect(featureForPath("/people/me")).toBeNull();
    expect(featureForPath(PEOPLE_HREF)).toBe(PEOPLE_FEATURE);
  });

  it("does NOT ride the tasks feature", () => {
    // The whole reason `people` exists as its own slug: a manager who needs the
    // org chart and the assignee picker should not have to be handed the
    // personal task app (My Tasks) to get them (§6).
    expect(featureForPath(PEOPLE_HREF)).not.toBe("tasks");
  });
});

describe("the People Center's directory sub-app", () => {
  const peopleCenter = CENTERS.find((c) => c.slug === "people");

  it("exists", () => {
    expect(peopleCenter).toBeTruthy();
  });

  it("points its directory entry at the live app", () => {
    // WS-13 asked for exactly this read view and it stayed `planned` until now.
    const entry = peopleCenter?.apps.find((a) =>
      a.label.toLowerCase().includes("directory")
    );
    expect(entry, "the People Center has no directory entry").toBeTruthy();
    expect(entry?.status).toBe("live");
    expect(entry?.href).toBe(PEOPLE_HREF);
  });

  it("links at the app's own path, never a Center-forked one", () => {
    // A Center item is (app + scope). Forking the app per department is the
    // bloat failure mode `department_centers.md` §1 rule 2 says to refuse in
    // review — the same rule the Projects entries follow.
    //
    // Sub-routes of the app are fine — `/people/me` is a ROW selector and
    // `/people/schedule` is a VIEW; both render the same app under the same
    // gate. What this catches is a path SCOPED BY CENTER (`/people/sales`),
    // which is the bloat failure mode, and it is asserted against the real
    // Center slugs rather than an allow-list somebody has to extend on every
    // ticket — a fence that needs editing to stay green stops being a fence.
    const CENTER_SLUGS = new Set(CENTERS.map((c) => c.slug));
    for (const center of CENTERS) {
      for (const app of center.apps) {
        if (!app.href?.startsWith(PEOPLE_HREF)) continue;
        const [, , sub] = app.href.split("?")[0].split("/");
        expect(
          sub === undefined || !CENTER_SLUGS.has(sub),
          `${app.href} scopes the People app by Center`
        ).toBe(true);
      }
    }
  });

  it("offers the working week as its own entry", () => {
    const entry = peopleCenter?.apps.find(
      (a) => a.href === `${PEOPLE_HREF}/schedule`
    );
    expect(entry, "the People Center has no 'working week' entry").toBeTruthy();
    expect(entry?.status).toBe("live");
  });

  it("offers the self-service profile as its own entry", () => {
    // Discoverability is the whole point: a person who does not know
    // `/people/me` exists will never edit their own record, and the Center
    // landing page is where they look.
    const entry = peopleCenter?.apps.find((a) => a.href === `${PEOPLE_HREF}/me`);
    expect(entry, "the People Center has no 'my profile' entry").toBeTruthy();
    expect(entry?.status).toBe("live");
  });
});
