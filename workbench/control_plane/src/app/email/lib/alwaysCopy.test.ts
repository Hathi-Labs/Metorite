/**
 * The "always copy" rules in the Email app's settings (§21.16).
 *
 * 1. The client reads and writes the WHOLE list through the gateway routes,
 *    and shows the server's refusal in its own words.
 * 2. The card sits with the other assistant settings, in the Advanced
 *    section, before the domains card (`noTierChoice.test.ts` holds the end).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getAlwaysCopy, saveAlwaysCopy } from "./api";

const HERE = fileURLToPath(new URL(".", import.meta.url));

afterEach(() => vi.unstubAllGlobals());

describe("the always-copy rules", () => {
  it("reads the rules of one mailbox", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return new Response(JSON.stringify({ rules: [{ match: "jain.ac.in", cc: ["kiran@acme.test"] }] }));
    });
    expect(await getAlwaysCopy("acc 1")).toEqual([{ match: "jain.ac.in", cc: ["kiran@acme.test"] }]);
    expect(calls).toEqual(["/api/email/assistant/always-copy?account_id=acc%201"]);
  });

  it("writes the whole list, and a refusal keeps the server's words", async () => {
    const sent: unknown[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      sent.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify({ detail: "Nothing changed. spy@evil.test is not a colleague." }), { status: 422 });
    });
    await expect(saveAlwaysCopy("acc-1", [{ match: "jain.ac.in", cc: ["spy@evil.test"] }])).rejects.toThrow(
      "spy@evil.test is not a colleague",
    );
    expect(sent).toEqual([{ account_id: "acc-1", rules: [{ match: "jain.ac.in", cc: ["spy@evil.test"] }] }]);
  });

  it("the card sits in the settings, before the domains card", () => {
    const src = readFileSync(`${HERE}/../components/automation/ai-settings/SettingsTab.tsx`, "utf8");
    const card = src.indexOf("<AlwaysCopyCard");
    expect(card).toBeGreaterThan(0);
    expect(card).toBeLessThan(src.indexOf("<OrgDomainsCard\n"));
    expect(src).toContain('title="Always copy"');
  });
});
