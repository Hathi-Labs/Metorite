/**
 * "Why can't I see that pane?" — the answer, as data.
 *
 * The claims worth pinning are the ones that distinguish causes a user
 * otherwise cannot tell apart. A page that said only "not available" would be
 * no better than the silent nothing it replaces.
 */

import { describe, expect, it } from "vitest";

import { NO_ACCESS, type Access } from "./access";
import { allPanes, paneReport, summarise, unmappedFeatures } from "./accessReport";

const signedIn = (over: Partial<Access> = {}): Access => ({
  ...NO_ACCESS,
  email: "vj@fracktal.in",
  authenticated: true,
  is_active: true,
  roles: ["member"],
  features: ["chat", "tasks"],
  features_denied: [
    { slug: "projects", permission: "feature:projects" },
    { slug: "people", permission: "feature:people" },
  ],
  ...over,
});

const find = (access: Access, href: string) =>
  paneReport(access).find((r) => r.href === href)!;

describe("paneReport", () => {
  it("covers every pane, not only the hidden ones", () => {
    // A list of failures alone cannot answer "is it hidden, or does it not
    // exist" — which is the question somebody actually has.
    expect(paneReport(signedIn())).toHaveLength(allPanes().length);
  });

  it("says which permission a hidden pane needs", () => {
    const row = find(signedIn(), "/projects");
    expect(row.status).toBe("denied");
    expect(row.permission).toBe("feature:projects");
    expect(row.reason).toContain("feature:projects");
  });

  it("names the roles that failed to grant it", () => {
    // "you need feature:projects" is only half an answer; the other half is
    // which of your roles was supposed to carry it.
    expect(find(signedIn(), "/projects").reason).toContain("member");
  });

  it("distinguishes a deny override from a missing grant", () => {
    // The one case where "ask an admin to grant it" is advice that cannot
    // work, so saying it would send somebody down a dead end.
    const access = signedIn({
      denied: ["feature:projects"],
      features_denied: [{ slug: "projects", permission: "feature:projects" }],
    });
    expect(find(access, "/projects").reason).toMatch(/deny override/i);
    expect(find(access, "/projects").reason).toMatch(/will not help/i);
  });

  it("marks a granted pane as reachable", () => {
    expect(find(signedIn(), "/tasks").status).toBe("granted");
  });

  it("treats an ungated pane as open to everyone", () => {
    // `/access` itself — it must never report as denied, or the diagnosis page
    // would be telling you that you cannot read the diagnosis page.
    expect(find(signedIn({ features: [] }), "/access").status).toBe("granted");
  });

  it("says signed-out rather than denied when nothing is resolved", () => {
    // Otherwise a gateway outage reads as "an admin took your access away".
    const rows = paneReport(NO_ACCESS);
    expect(rows.every((r) => r.status === "signed-out")).toBe(true);
  });
});

describe("summarise", () => {
  it("counts what is reachable against what is OFFERED, not the whole registry", () => {
    const text = summarise(paneReport(signedIn()));
    expect(text).toMatch(/apps available/);
    expect(text).toMatch(/need a grant/);
    // Unlaunched panes are reported separately and kept out of the
    // denominator — "3 of 24" would read as broken when sixteen of the 24 are
    // simply not being offered to anybody yet (LS-3).
    expect(text).toMatch(/not available yet/);
  });

  it("says so plainly when everything offered is available", () => {
    const all = allPanes()
      .map((p) => p.feature)
      .filter((f): f is string => Boolean(f));
    const text = summarise(paneReport(signedIn({ features: all, features_denied: [] })));
    expect(text).toMatch(/All \d+ available apps are open to you/);
  });

  it("leads with signed-out, because nothing else is meaningful then", () => {
    expect(summarise(paneReport(NO_ACCESS))).toMatch(/Signed out/i);
  });
});

describe("unmappedFeatures", () => {
  it("finds a granted feature the sidebar never points at", () => {
    // Reachable by URL and undiscoverable in the UI — which also reads as
    // "the app doesn't have that", and is invisible from the pane list.
    expect(unmappedFeatures(signedIn({ features: ["chat", "ghostpane"] }))).toEqual([
      "ghostpane",
    ]);
  });

  it("is empty when every grant has a home", () => {
    expect(unmappedFeatures(signedIn({ features: ["chat", "tasks"] }))).toEqual([]);
  });
});

describe("launch status is reported apart from access (LS-3 · D49)", () => {
  it("says NOT AVAILABLE YET for a preview pane the member actually holds", () => {
    // The case that makes this status worth having: the member has
    // `feature:email`, so the old report said "granted" for a pane that is not
    // in the menu — or, if we had hidden it by revoking the grant, "you need
    // feature:email", which is advice that would not have worked either.
    const rows = paneReport(signedIn({ features: ["email", "chat"], features_denied: [] }));
    const email = rows.find((r) => r.href === "/email");
    expect(email?.status).toBe("not-launched");
    expect(email?.reason).toMatch(/not available yet/i);
    expect(email?.reason).toMatch(/grant will not reveal it/i);

    // ...while a live pane with the same grant is plainly granted.
    expect(rows.find((r) => r.href === "/chat")?.status).toBe("granted");
  });

  it("still reports every pane, so 'hidden' stays distinguishable from 'absent'", () => {
    const rows = paneReport(signedIn());
    expect(rows.some((r) => r.status === "not-launched")).toBe(true);
    expect(rows.length).toBe(allPanes().length);
  });

  it("does not call a preview pane's slug unmapped", () => {
    // It has a nav entry; it is just not offered. Reporting it as unmapped
    // would send somebody to write a nav entry that already exists.
    expect(unmappedFeatures(signedIn({ features: ["email"] }))).not.toContain("email");
  });
});
