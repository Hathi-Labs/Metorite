import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { STATUS_CATEGORIES, taskMetaForPeople } from "./projectToolRows";

describe("taskMetaForPeople", () => {
  it("drops the bare category that restates the status", () => {
    expect(
      taskMetaForPeople("status «To do» · todo · due 2026-09-30 · «a@x.io» · in «Apollo»"),
    ).toBe("status To do · due 2026-09-30 · a@x.io · in Apollo");
    expect(taskMetaForPeople("status «Shipped» · done · done")).toBe("status Shipped · done");
  });

  it("keeps a category word that is not right after the status", () => {
    expect(taskMetaForPeople("due 2026-09-30 · done")).toBe("due 2026-09-30 · done");
    expect(taskMetaForPeople("todo")).toBe("todo");
  });

  it("keeps a status name that itself holds a separator", () => {
    expect(taskMetaForPeople("status «Review · todo» · in_progress · due 2026-09-30")).toBe(
      "status Review · todo · due 2026-09-30",
    );
  });

  it("knows the same categories as the gateway", () => {
    const core = readFileSync(
      resolve(__dirname, "../../../../apps/services/gateway/gateway/routes/projects/core.py"),
      "utf8",
    );
    const block = core.slice(core.indexOf("STATUS_CATEGORIES"), core.indexOf(")", core.indexOf("STATUS_CATEGORIES")));
    const py = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    expect(py).toEqual([...STATUS_CATEGORIES]);
  });

  it("is what the task card draws", () => {
    const src = readFileSync(resolve(__dirname, "../components/projects/ProjectToolCards.tsx"), "utf8");
    expect(src).toContain("{taskMetaForPeople(row.meta)}");
  });
});
