// WS-17 EM-T4f, fix round 1 — Remove on the Integrations Email tab.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.6, EM-T4f.
// `handleDelete` sent the DELETE, read no status, and took the mailbox off the
// list on a 500. vitest here runs in the node environment and cannot render
// the page. So a source scan with the comments removed holds the wiring, and
// `app/email/lib/connect.test.ts` runs the shared decision
// (`disconnectFailureText`) and `deleteEmailAccount` against a stubbed fetch.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const PAGE = readFileSync(join(__dirname, "page.tsx"), { encoding: "utf-8" })
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

/** The body of `const handleDelete = useCallback(...)` in the Email tab. */
function emailHandleDelete(): string {
  const tab = PAGE.indexOf("function EmailTab()");
  expect(tab, "EmailTab not found").toBeGreaterThan(-1);
  const start = PAGE.indexOf("const handleDelete = useCallback", tab);
  expect(start, "handleDelete not found in EmailTab").toBeGreaterThan(-1);
  const open = PAGE.indexOf("{", PAGE.indexOf("=>", start));
  let depth = 0;
  for (let i = open; i < PAGE.length; i++) {
    if (PAGE[i] === "{") depth++;
    else if (PAGE[i] === "}") {
      depth--;
      if (depth === 0) return PAGE.slice(open, i + 1);
    }
  }
  throw new Error("unbalanced braces in handleDelete");
}

describe("Remove on the Integrations Email tab (EM-T4f)", () => {
  it("goes through deleteEmailAccount, which throws when the status is not ok", () => {
    const body = emailHandleDelete();
    expect(PAGE).toMatch(/import \{ deleteEmailAccount \} from "@\/app\/email\/lib\/api";/);
    expect(body).toContain("await deleteEmailAccount(id);");
    expect(body).not.toMatch(/fetch\(/);
  });

  it("drops the mailbox only after the delete succeeded", () => {
    const body = emailHandleDelete();
    const call = body.indexOf("await deleteEmailAccount(id);");
    const drop = body.indexOf("setAccounts((prev) => prev.filter((a) => a.id !== id));");
    const handler = body.indexOf("catch (e)");
    expect(call).toBeGreaterThan(-1);
    expect(drop).toBeGreaterThan(call);
    expect(handler).toBeGreaterThan(drop);
    expect(body.slice(handler)).not.toContain("setAccounts");
  });

  it("shows the reason of the gateway above the list, and keeps the list", () => {
    const body = emailHandleDelete();
    expect(body).toContain("setRemoveError(disconnectFailureText(e));");
    // The load error replaces the whole list, so a refused remove must not use it.
    expect(body).not.toContain("setError(");
    expect(PAGE).toMatch(/\{removeError && \(\s*<div role="alert"/);
    expect(PAGE).toMatch(/<p className="flex-1 min-w-0">\{removeError\}<\/p>/);
  });
});
