/**
 * WS-61 KB-2q: the rules of the space forms. Each case is written so the
 * obvious wrong build fails it.
 */
import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import { PENDING_ME_ID, withPendingMe } from "./labels";
import {
  DEFAULT_SPACE_ICON,
  SPACE_ADVICE,
  SPACE_ICON_CHOICES,
  canEditSpace,
  createSpaceBody,
  patchSpaceBody,
} from "./spaceForm";
import type { Space } from "./types";

const team = (over: Partial<Space> = {}): Space => ({
  id: "ks-1", slug: "team-x", name: "Sales", purpose: "", icon: "", kind: "team", owner_email: null,
  archived_at: null, created_at: null, level: "manage", page_count: 0, ...over,
});

describe("who may open Space settings", () => {
  it("a manager of a team space may", () => {
    expect(canEditSpace(team())).toBe(true);
  });
  it("a writer or a reader may not", () => {
    expect(canEditSpace(team({ level: "write" }))).toBe(false);
    expect(canEditSpace(team({ level: "read" }))).toBe(false);
  });
  it("a me space may not, whatever the level", () => {
    expect(canEditSpace(team({ kind: "me" }))).toBe(false);
  });
  it("the stand-in My notes may not, and its id never reaches an edit", () => {
    const stand = withPendingMe([]).find((s) => s.id === PENDING_ME_ID)!;
    expect(canEditSpace(stand)).toBe(false);
    expect(canEditSpace(team({ id: PENDING_ME_ID }))).toBe(false);
  });
});

describe("the bodies", () => {
  it("a create trims and folds the name, and sends no grants", () => {
    const body = createSpaceBody({ name: "  Sales   playbooks ", icon: " Rocket ", purpose: " For sales. " });
    expect(body).toEqual({ name: "Sales playbooks", icon: "Rocket", purpose: "For sales." });
    expect(Object.keys(body)).not.toContain("grants");
  });
  it("a patch holds only what changed", () => {
    const saved = { name: "Sales", icon: "Users", purpose: "A" };
    expect(patchSpaceBody(saved, { name: "Sales", icon: "Users", purpose: "A" })).toBeNull();
    expect(patchSpaceBody(saved, { name: " Sales ", icon: "Rocket", purpose: "A" })).toEqual({ icon: "Rocket" });
    expect(patchSpaceBody(saved, { name: "Revenue", icon: "Users", purpose: "" })).toEqual({ name: "Revenue", purpose: "" });
  });
  it("a blank name is never a change", () => {
    expect(patchSpaceBody({ name: "Sales", icon: "", purpose: "" }, { name: "  ", icon: "", purpose: "" })).toBeNull();
  });
});

describe("the form words and icons", () => {
  it("says when to make a space and when to make a page", () => {
    expect(SPACE_ADVICE).toBe(
      "Make a space when a different group of people needs to see it. For a new topic, make a page.",
    );
  });
  it("every offered icon is a real icon, and the default is offered", () => {
    for (const name of SPACE_ICON_CHOICES) expect(isKnownIcon(name), `${name} is not an icon`).toBe(true);
    expect(SPACE_ICON_CHOICES).toContain(DEFAULT_SPACE_ICON);
    expect(new Set(SPACE_ICON_CHOICES).size).toBe(SPACE_ICON_CHOICES.length);
  });
});
