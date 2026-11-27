import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const src = readFileSync("src/components/ArtifactViewerModal.tsx", "utf-8");
const tag = src.match(/<Button[^>]*onClick=\{onClose\}[^>]*\/>/)?.[0] ?? "";

describe("ArtifactViewerModal close control", () => {
  it("is a labelled shared Button with an X icon, not a text glyph", () => {
    expect(tag).toContain('icon="X"');
    expect(tag).toContain("aria-label={`Close ${entry.name}`}");
    expect(tag).toContain("text-muted-foreground");
    expect(tag).toContain("hover:text-foreground");
  });

  it("is not hidden at rest", () => {
    expect(tag).not.toBe("");
    expect(tag).not.toMatch(/opacity-0|invisible|hidden|group-hover/);
  });
});
