/**
 * My Tasks draws a tag and a lane in the colour Projects draws them
 * (continuity P3, items 1 and 2).
 *
 * Before: `taskMetaChips` passed tag NAMES only, so `resolveHue(undefined)`
 * drew every tag grey. `StatusPill` coloured by category only, so a lane an
 * owner coloured violet drew violet on its board and blue in My Tasks.
 * AGENTS.md rule 5 names that failure.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { accentForStatus } from "@/app/projects/lib/accent";
import { resolveHue } from "@/lib/statusAccent";

import { taskMetaChips } from "./cardMeta";
import { mapLensItem } from "./lens";
import { categoryAccent, laneAccent } from "./stageColors";
import type { MyTask } from "./types";

const NOW = Date.parse("2026-09-25T12:00:00Z");

function item(patch: Partial<MyTask>): MyTask {
  return {
    id: "t1",
    source: "LOCAL",
    title: "A task",
    disposition: "NEXT",
    isMine: true,
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-01T00:00:00Z",
    ...patch,
  };
}

describe("the lens row carries the colours", () => {
  it("maps status_color and tag_colors, keys lower-cased", () => {
    const mapped = mapLensItem({
      id: "t1",
      title: "x",
      tags: ["Bug"],
      status_color: "violet",
      tag_colors: { Bug: "red", ops: "amber", bad: 3 },
    });
    expect(mapped.statusColor).toBe("violet");
    // A non-string colour is dropped, never drawn as "3".
    expect(mapped.tagColors).toEqual({ bug: "red", ops: "amber" });
  });

  it("a null map (no registered tag) is an empty map", () => {
    expect(mapLensItem({ id: "t1", title: "x", tag_colors: null }).tagColors).toEqual({});
  });
});

describe("a tag chip wears its registry colour", () => {
  it("resolves the hue the Projects card resolves, case-insensitively", () => {
    const chips = taskMetaChips(
      item({ tags: ["Bug", "loose"], tagColors: { bug: "red" } }),
      NOW,
    );
    const bug = chips.find((c) => c.key === "tags:Bug");
    const loose = chips.find((c) => c.key === "tags:loose");
    expect(bug?.hue).toBe(resolveHue({ color: "red" }));
    expect(bug?.hue).toBe("red");
    // Unregistered: grey, exactly as Projects draws a tag with no colour.
    expect(loose?.hue).toBe(resolveHue({ color: undefined }));
  });
});

describe("a lane pill wears the lane's own colour first", () => {
  it("a custom-coloured lane matches Projects' accentForStatus", () => {
    const lane = { statusColor: "violet", statusCategory: "in_progress" };
    expect(laneAccent(lane)).toEqual(
      accentForStatus({ color: "violet", category: "in_progress" }),
    );
    // And it is NOT the category colour, which is what the pill drew before.
    expect(laneAccent(lane)).not.toEqual(categoryAccent("in_progress"));
  });

  it("with no stored colour it falls back to the category", () => {
    expect(laneAccent({ statusCategory: "done" })).toEqual(categoryAccent("done"));
    expect(laneAccent({}, "in_progress")).toEqual(categoryAccent("in_progress"));
  });

  it("the pill draws through laneAccent (source fence: .tsx is not collected)", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/StatusPill.tsx", import.meta.url)),
      "utf8",
    );
    expect(src).toMatch(/const accent = laneAccent\(item, currentCategory\)/);
  });
});
