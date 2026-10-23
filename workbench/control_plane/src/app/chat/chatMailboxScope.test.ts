// Multi-inbox UI, item 1: /chat uses the shared scope of the email pane.
// Fence `chat-app-shared-scope`. Vitest here runs in node and cannot render
// the page, so a source scan proves the wiring and chatScope.test.ts proves
// the decisions.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = readFileSync(join(__dirname, "page.tsx"), "utf-8").replace(/\r\n/g, "\n");
const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

describe("chat-app-shared-scope", () => {
  it("derives the scope with chatScope(), the one rule of the email pane", () => {
    expect(code).toContain("chatScope(");
    expect(code).toContain("chatMailboxOptions(");
    expect(code).toContain("readPreferredAccountId()");
    expect(code).not.toContain("emailAccounts[0]");
  });

  it("reads accounts through the mapped client, so labels and slots arrive", () => {
    expect(code).toContain("listEmailAccounts()");
  });

  it("tells the persona and the tools when the scope is All inboxes", () => {
    expect(code).toMatch(/allInboxes:\s*emailChatAllInboxes/);
    expect(code).toContain("chatSettingsRead(");
    expect(code).toMatch(/accountId:\s*emailChatAccountId/);
  });
});
