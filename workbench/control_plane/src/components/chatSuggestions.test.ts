/**
 * The chat's follow-up suggestions must not offer a retired connector.
 *
 * ClickUp is gone (D52): no connector, no importer, no sync. On 2026-09-28 the
 * orchestrator still offered "Create a task in ClickUp" as a one-click
 * follow-up, so a member was invited to ask for something the product cannot
 * do. The suggestions are string literals in AgentChat.tsx, so this reads the
 * source and refuses any quoted string that names ClickUp.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(join(__dirname, "AgentChat.tsx"), "utf-8");

describe("chat follow-up suggestions", () => {
  it("offer no retired connector (D52)", () => {
    const quoted = SOURCE.match(/"[^"\n]*"/g) ?? [];
    const retired = quoted.filter((s) => /clickup/i.test(s));
    expect(retired).toEqual([]);
  });
});
