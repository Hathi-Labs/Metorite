// The page of /studio/mcp-tools (B1b-4) is read as source, as the other studio page fences do.
// R7 mutations, each run once:
//  - draw the description with dangerouslySetInnerHTML -> "draws text only" fails.
//  - pass a recomputed hash to approveTool             -> "sends the hash the row showed" fails.
//  - drop the `canApprove` check on the button         -> "the Approve button is behind canApprove" fails.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = readFileSync(join(__dirname, "page.tsx"), "utf-8");

describe("/studio/mcp-tools page", () => {
  it("draws text only", () => {
    expect(src).not.toMatch(/dangerouslySetInnerHTML|innerHTML|react-markdown|from "marked"/i);
  });
  it("sends the hash the row showed", () => {
    expect(src).toMatch(/approveTool\(tool\.server, tool\.tool, tool\.textHash\)/);
    expect(src).not.toMatch(/crypto|subtle|sha256|createHash/i);
  });
  it("the Approve button is behind canApprove with the member's capabilities", () => {
    expect(src).toMatch(/canApprove\(t, access\.capabilities\)/);
  });
  it("the confirm dialog holds the tool text", () => {
    expect(src).toMatch(/<ConfirmDialog[\s\S]*copy\.description[\s\S]*<\/ConfirmDialog>/);
  });
  it("builds no control, colour or px value", () => {
    expect(src).not.toMatch(/<button|<select|<input|#[0-9a-fA-F]{3,6}\b|\d+px/);
  });
});
