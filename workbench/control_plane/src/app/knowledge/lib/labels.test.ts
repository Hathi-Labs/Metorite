/**
 * The words of Knowledge and the rules of its forms (WS-61 KB-2a). Fence for
 * `labels.ts`.
 */
import { describe, expect, it } from "vitest";

import {
  MY_NOTES,
  MY_NOTES_CHOICE,
  REASON_LABELS,
  canWriteIn,
  createBody,
  dayLabel,
  defaultSpaceChoice,
  pageCount,
  patchBody,
  reasonLabel,
  spaceIcon,
  stateLabel,
  stateTone,
  writableSpaces,
} from "./labels";
import type { Space } from "./types";

function space(id: string, over: Partial<Space> = {}): Space {
  return {
    id,
    slug: id,
    name: id,
    purpose: "",
    icon: "",
    kind: "team",
    owner_email: null,
    archived_at: null,
    created_at: null,
    level: "read",
    page_count: 0,
    ...over,
  };
}

describe("the state labels", () => {
  it("names each state of §4.2 in words, with a tone that is not the accent", () => {
    expect(stateLabel("draft")).toBe("Draft");
    expect(stateLabel("published")).toBe("Published");
    expect(stateLabel("archived")).toBe("Archived");
    expect(stateTone("published")).toBe("success");
    expect(stateTone("draft")).toBe("warning");
    expect(["draft", "published", "archived"].map(stateTone)).not.toContain("primary");
  });

  it("reads an unknown state as itself, with a neutral tone", () => {
    expect(stateLabel("odd")).toBe("odd");
    expect(stateTone("odd")).toBe("neutral");
  });
});

describe("the version reasons", () => {
  it("names the four reasons the acceptance reads: create, edit, publish and revert", () => {
    expect(["create", "edit", "publish", "revert"].map(reasonLabel)).toEqual([
      "Created",
      "Edited",
      "Published",
      "Reverted",
    ]);
  });

  it("names every reason of kb_page_versions", () => {
    for (const reason of ["create", "edit", "publish", "move", "archive", "restore", "revert", "agent_proposal", "accept", "import"]) {
      expect(REASON_LABELS[reason], reason).toBeTruthy();
    }
  });
});

describe("the levels", () => {
  it("lets write and manage write, and read not", () => {
    expect(canWriteIn("write")).toBe(true);
    expect(canWriteIn("manage")).toBe(true);
    expect(canWriteIn("read")).toBe(false);
    expect(canWriteIn(null)).toBe(false);
  });
});

describe("the New page form", () => {
  const company = space("c", { kind: "company", name: "Company", level: "read" });
  const team = space("t", { name: "Ops", level: "write" });

  it("offers the writable spaces, and My notes before the member has a me space", () => {
    expect(writableSpaces([company, team])).toEqual([
      { value: "t", label: "Ops" },
      { value: MY_NOTES_CHOICE, label: MY_NOTES },
    ]);
  });

  it("offers the me space itself once it exists", () => {
    const me = space("m", { kind: "me", name: "My notes", level: "manage" });
    expect(writableSpaces([company, me])).toEqual([{ value: "m", label: "My notes" }]);
  });

  it("starts in the space on screen when the member can write there", () => {
    expect(defaultSpaceChoice([company, team], "t")).toBe("t");
  });

  it("starts in My notes when the space on screen is read only", () => {
    expect(defaultSpaceChoice([company, team], "c")).toBe(MY_NOTES_CHOICE);
    const me = space("m", { kind: "me", level: "manage" });
    expect(defaultSpaceChoice([company, me], "c")).toBe("m");
  });

  it("sends no space for My notes, so the page lands in me", () => {
    expect(createBody({ space: MY_NOTES_CHOICE, title: "  Refunds " })).toEqual({ title: "Refunds" });
  });

  it("sends the space and the parent of a sub-page", () => {
    expect(createBody({ space: "t", parentId: "p1", title: "Child" })).toEqual({
      space_id: "t",
      parent_id: "p1",
      title: "Child",
    });
  });
});

describe("patchBody", () => {
  const saved = { title: "Refunds", body_md: "Old" };

  it("sends only what changed", () => {
    expect(patchBody(saved, { title: "Refunds", body_md: "New" })).toEqual({ body_md: "New" });
    expect(patchBody(saved, { title: " Returns ", body_md: "Old" })).toEqual({ title: "Returns" });
  });

  it("sends nothing when nothing changed, or when only spaces were added to the title", () => {
    expect(patchBody(saved, { title: "Refunds ", body_md: "Old" })).toBeNull();
  });

  it("never sends an empty title", () => {
    expect(patchBody(saved, { title: "   ", body_md: "Old" })).toBeNull();
  });
});

describe("the small words", () => {
  it("counts pages", () => {
    expect(pageCount(1)).toBe("1 page");
    expect(pageCount(0)).toBe("0 pages");
    expect(pageCount(3)).toBe("3 pages");
  });

  it("writes a day that reads the same in India and the US", () => {
    expect(dayLabel("2027-04-16T10:00:00Z")).toBe("16 Apr 2027");
    expect(dayLabel(null)).toBe("");
    expect(dayLabel("not a date")).toBe("");
  });

  it("gives a space its own icon, else the icon of its kind", () => {
    expect(spaceIcon({ icon: "Rocket", kind: "team" })).toBe("Rocket");
    expect(spaceIcon({ icon: "", kind: "company" })).toBe("Building2");
    expect(spaceIcon({ icon: "", kind: "me" })).toBe("Lock");
  });
});
