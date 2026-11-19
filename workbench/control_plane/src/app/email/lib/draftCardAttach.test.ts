import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// Task 3812: the reply card of a conversation (DraftCard) had no attach
// control, so a draft that said "attached" could carry no file. A source fence,
// as the card needs the whole store to render.
describe("DraftCard attach", () => {
  const src = readFileSync(
    join(process.cwd(), "src/app/email/components/ConversationView.tsx"), "utf-8");
  it("has the computer pick and the Metorite picker", () => {
    expect(src).toContain('type="file"');
    expect(src).toContain("<ArtifactAttachPicker");
  });
  it("sends the files with the draft save", () => {
    expect(src).toMatch(/attachments: freshAtt\.length \? freshAtt : undefined/);
    expect(src).toMatch(/artifacts: freshArt\.length \? freshArt : undefined/);
  });
  it("keeps the files out of the autosave, which would double them", () => {
    expect(src).toContain("}, [body, quote, to, cc, bcc]);");
  });
});
