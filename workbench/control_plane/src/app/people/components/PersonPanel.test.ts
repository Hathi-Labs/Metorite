/**
 * The person panel's draft belongs to ONE person.
 *
 * `PersonPanel` keeps the old person on screen while it fetches the next one,
 * so `ProfilePanels` never unmounts between them. Without a key its unsaved
 * draft survived the switch, and a Save on person B sent the edits made to
 * person A. A grep, because the failure needs two fetches and a save to show.
 */

import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

const SOURCE = fs.readFileSync(path.join(__dirname, "PersonPanel.tsx"), "utf-8");

describe("PersonPanel", () => {
  it("keys ProfilePanels by the person, so a draft cannot follow you to the next one", () => {
    expect(SOURCE).toMatch(/<ProfilePanels\s+key=\{person\.id\}/);
  });
});
