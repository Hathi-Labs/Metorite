/**
 * The tag manager's confirmations: true words, one dialog (2026-09-24).
 *
 * A tag delete strips the tag from every task and cannot be undone. The
 * tasks themselves stay. An org-wide rename states how many tasks it rewrites.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { orgRenameCopy, tagDeleteCopy } from "./tagCopy";

const manager = () =>
  readFileSync(resolve(__dirname, "..", "components", "TagManager.tsx"), "utf-8").replace(
    /\r\n/g,
    "\n",
  );

describe("the words", () => {
  it("a delete takes the tag off its tasks, keeps the tasks, and cannot be undone", () => {
    const copy = tagDeleteCopy({ name: "bug", task_count: 3 });
    expect(copy.subject).toBe("bug");
    expect(copy.body).toBe(
      "It is taken off 3 open tasks, and off any archived ones, and deleted for good. The tasks stay. This cannot be undone.",
    );
    // The count excludes archived tasks, and the delete strips them too, so
    // a zero count must never claim that no task wears the tag.
    const none = tagDeleteCopy({ name: "x", task_count: 0 }).body;
    expect(none).toMatch(/^No open task wears it\./);
    expect(none).toMatch(/archived/);
    expect(none).not.toMatch(/^No task wears it/);
  });

  it("an org-wide rename states the count, or says it could not be read", () => {
    const copy = orgRenameCopy("Bug", { tag: "bug", tasks: 340, projects: 11 });
    expect(copy.body).toBe("Renaming it to “Bug” rewrites 340 tasks across 11 projects.");
    expect(copy.note).toMatch(/projects you may not be able to open/);
    expect(orgRenameCopy("Bug", null).body).toMatch(/could not be read/);
  });
});

describe("the manager asks through the shared dialog", () => {
  it("has no window.confirm, and delete no longer runs on one click", () => {
    const src = manager();
    expect(src).not.toMatch(/window\.confirm\(/);
    expect(src).toMatch(/onClick=\{\(\) => setConfirming\(\{ kind: "delete", tag: t \}\)\}/);
    expect(src).toMatch(/<ConfirmDialog\b/);
    // The delete request is sent only from the dialog's confirm.
    const confirm = src.slice(src.indexOf("onConfirm={() => {"));
    expect(confirm).toMatch(/projectsApi\.deleteTag\(act\.tag\.id\)/);
    expect(src.match(/projectsApi\.deleteTag\(/g) ?? []).toHaveLength(1);
  });

  it("an org-wide rename is asked with the count before the write", () => {
    const src = manager();
    expect(src).toMatch(/setConfirming\(\{ kind: "rename", tag: t, to: next, impact \}\)/);
    expect(src).toMatch(/orgRenameCopy\(confirming\.to, confirming\.impact\)/);
  });
});
