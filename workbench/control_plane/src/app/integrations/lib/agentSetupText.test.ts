// MCP-Gc. The copy control of the AI agents view holds the setup text in a
// TS constant. This test fails when that constant differs from the fenced
// block of the playbook file, byte for byte. The python test
// `tests/unit/test_external_agents_paste_in_block.py` pins the doc the same way.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { AGENT_SETUP_TEXT } from "./agentSetupText";

const KIT = resolve(
  process.cwd(),
  "../../packages/acb_playbook/acb_playbook/content/kit/paste-in.md",
);

describe("AGENT_SETUP_TEXT", () => {
  it("equals the fenced block of kit/paste-in.md", () => {
    const text = readFileSync(KIT, "utf-8").replace(/\r\n/g, "\n");
    const blocks = [...text.matchAll(/```text\n([\s\S]*?)\n```/g)];
    expect(blocks).toHaveLength(1);
    expect(AGENT_SETUP_TEXT).toBe(`${blocks[0][1]}\n`);
  });

  it("fits the 3,000 character limit", () => {
    expect(AGENT_SETUP_TEXT.length).toBeLessThanOrEqual(3001);
  });
});
