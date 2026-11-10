/**
 * A quiet file event: no file card in the message, still a row in the Files
 * tree (spec `projects_ai_chat.md` §14.10, re-review of #1064).
 *
 * `make_report` writes a PDF and a `.report.json`, and the report card shows
 * both. The server sends their `artifact_created` events with `"quiet": true`
 * (`write_artifact._notify(announce=False)`). The message must not draw two
 * more file cards for them, and the open Files tree must list them with no
 * reload. The last block reads the chat's source, so the tree keeps the one
 * merge rule and a quiet file never opens the side panel by itself.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import { fileCardsOf, isQuietArtifact } from "./artifactCards";
import { autoOpenArtifact } from "./autoOpenArtifact";
import { mergeFileEntry } from "./shell/chatHistory";

const PDF = "outputs/q3-review.pdf";
const JSON_FILE = "outputs/q3-review.report.json";

describe("fileCardsOf: the message side", () => {
  it("draws a card for a plain file event", () => {
    const cards = fileCardsOf([
      { name: "artifact_created", value: { path: "outputs/a.md", size: 3, mime_type: "text/markdown" } },
    ]);
    expect(cards).toEqual([
      { path: "outputs/a.md", name: "a.md", size: 3, mimeType: "text/markdown", sha256: undefined },
    ]);
  });

  it("draws no card for a quiet file event", () => {
    const cards = fileCardsOf([
      { name: "artifact_created", value: { path: PDF, quiet: true } },
      { name: "artifact_created", value: { path: JSON_FILE, quiet: true } },
      { name: "artifact_created", value: { path: "outputs/notes.md" } },
    ]);
    expect(cards.map((c) => c.path)).toEqual(["outputs/notes.md"]);
  });

  it("keeps one card for a file written twice, and skips other events", () => {
    const cards = fileCardsOf([
      { name: "artifact_created", value: { path: "outputs/a.md", size: 1 } },
      { name: "artifact_updated", value: { path: "outputs/a.md", size: 2 } },
      { name: "generative_ui", value: { type: "template" } },
    ]);
    expect(cards).toHaveLength(1);
    expect(cards[0].size).toBe(2);
  });

  it("reads quiet only as the boolean true", () => {
    expect(isQuietArtifact({ quiet: true })).toBe(true);
    expect(isQuietArtifact({ quiet: "true" })).toBe(false);
    expect(isQuietArtifact({})).toBe(false);
    expect(isQuietArtifact(null)).toBe(false);
  });
});

describe("the Files tree side", () => {
  it("lists a quiet file at once, with its size", () => {
    const tree = mergeFileEntry([], { path: PDF, size: 2048, mimeType: "application/pdf" }, "t1");
    const next = mergeFileEntry(tree, { path: JSON_FILE, size: 512 }, "t2");
    expect(next.map((f) => f.path)).toEqual([PDF, JSON_FILE]);
    expect(next[0].size).toBe(2048);
  });

  it("never opens a quiet file in the side panel by itself", () => {
    const deps = { openDoc: vi.fn(), setDocLive: vi.fn(), schedule: vi.fn() };
    const opened = autoOpenArtifact(
      "outputs/q3-review.md", { sessionId: "s1", isMobile: false, quiet: true }, deps,
    );
    expect(opened).toBe(false);
    expect(deps.openDoc).not.toHaveBeenCalled();
  });
});

describe("the chat wires both sides", () => {
  const read = (rel: string) =>
    readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");

  it("MessageBubble draws its file cards through fileCardsOf", () => {
    const src = read("../components/MessageBubble.tsx");
    expect(src).toContain("fileCardsOf(message.customEvents)");
  });

  it("the hook carries quiet, and /chat merges every file into its tree", () => {
    const hook = read("../hooks/useAgentChat.ts");
    expect(hook.match(/quiet: isQuietArtifact\(data\)/g)?.length).toBe(2);
    const page = read("../app/chat/page.tsx");
    expect(page).toContain("mergeFileEntry(prev, entry,");
    expect(page).toContain("quiet: entry.quiet");
  });
});
