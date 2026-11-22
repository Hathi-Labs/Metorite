import { describe, expect, it } from "vitest";
import { archiveBar } from "./archiveBar";
import type { ProjectRow } from "./api";

const row = (id: string, extra: Partial<ProjectRow> = {}): ProjectRow =>
  ({ id, name: id, children: [], ...extra }) as ProjectRow;

describe("archiveBar", () => {
  it("says nothing for a live row", () => {
    expect(archiveBar(row("a"), [row("a")])).toBeNull();
  });

  it("words an archive root", () => {
    const a = row("a", { archived_at: "2026-11-08T10:00:00Z", archived_root_id: "a" });
    const bar = archiveBar(a, [a]);
    expect(bar?.text).toBe("Archived on 8 Nov 2026. Restore it to change its work.");
    expect(bar?.rootId).toBe("a");
  });

  it("names the ancestor for a row filed with its parent", () => {
    const stamp = { archived_at: "2026-11-08T10:00:00Z" };
    const child = row("c", { ...stamp, archived_root_id: "p" });
    const parent = row("p", { ...stamp, name: "Dragon 400", archived_root_id: "p", children: [child] });
    const bar = archiveBar(child, [parent]);
    expect(bar?.text).toBe(
      "Archived with Dragon 400 on 8 Nov 2026. Restore it to change its work."
    );
    expect(bar?.rootId).toBe("p");
    expect(bar?.root?.id).toBe("p");
  });
});
