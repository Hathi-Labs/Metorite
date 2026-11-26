// WS-CAL CE-10c: the words of the How to connect panel (docs/style_ste.md).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  ADMIN_NOTE,
  ALSO_SYNC_STEPS,
  ALSO_SYNC_TITLE,
  ENTRA_ADMIN_PATH,
  FULL_GUIDE_PATH,
  GOOGLE_ADMIN_PATH,
  GUIDE_LISTS,
  SYNC_NOTES,
  guideSentences,
} from "./connectGuide";

describe("the connect guide", () => {
  it("holds the four lists, each with steps", () => {
    expect(GUIDE_LISTS.map((l) => l.id)).toEqual(["google-personal", "google-workspace", "outlook-personal", "microsoft-365"]);
    for (const l of GUIDE_LISTS) expect(l.steps.length).toBeGreaterThan(0);
    expect(SYNC_NOTES.length).toBe(5);
  });

  it("keeps every sentence under 20 words", () => {
    for (const s of guideSentences()) {
      expect(s.split(/\s+/).length, s).toBeLessThan(20);
    }
  });

  it("uses no semicolon", () => {
    const all = [...GUIDE_LISTS.flatMap((l) => [l.title, ...l.steps]), ...SYNC_NOTES, ALSO_SYNC_TITLE, ...ALSO_SYNC_STEPS, ADMIN_NOTE].join("\n");
    expect(all).not.toContain(";");
  });

  it("carries the admin paths and the note word for word", () => {
    expect(GOOGLE_ADMIN_PATH).toBe(
      "Google Admin console, Security, Access and data control, API controls, Manage third-party app access, allow Metorite.",
    );
    expect(ENTRA_ADMIN_PATH).toBe("Entra admin centre, Enterprise applications, Metorite, Permissions, Grant admin consent.");
    expect(ADMIN_NOTE).toBe(
      "Please approve the Metorite app for these delegated permissions: Calendars.ReadWrite, User.Read, offline_access.",
    );
    expect(GUIDE_LISTS[1].steps).toContain(GOOGLE_ADMIN_PATH);
    expect(GUIDE_LISTS[3].steps).toContain(ENTRA_ADMIN_PATH);
  });

  it("holds the Already connected your email steps in the sentence fence", () => {
    expect(ALSO_SYNC_STEPS.length).toBeGreaterThanOrEqual(3);
    expect(ALSO_SYNC_STEPS.length).toBeLessThanOrEqual(5);
    const all = new Set(guideSentences());
    expect(all.has("Open Calendar settings")).toBe(true);
  });

  it("names the long guide as plain text", () => {
    expect(FULL_GUIDE_PATH).toBe("docs/calendar_connect.md");
  });

  // CE-14. The long guide and the panel must not drift. The panel words live in
  // this module. The guide must carry the admin note word for word and name each
  // part of both admin paths. Mutation: change one word of ADMIN_NOTE, or drop
  // "Grant admin consent" from the guide, and this test turns red.
  it("keeps the long guide in step with the panel", () => {
    const url = new URL("../../../../../../docs/calendar_connect.md", import.meta.url);
    const guide = readFileSync(fileURLToPath(url), "utf-8")
      .replace(/^> ?/gm, "")
      .replace(/\s+/g, " ");
    expect(guide).toContain(ADMIN_NOTE);
    const parts = (path: string) => path.replace(/\.$/, "").split(", ").map((x) => x.replace(/^allow /, ""));
    for (const part of [...parts(GOOGLE_ADMIN_PATH), ...parts(ENTRA_ADMIN_PATH)]) {
      expect(guide, part).toContain(part.replace("centre", "center"));
    }
  });
});
