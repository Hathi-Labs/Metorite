/**
 * The Artifacts app's Chats tab (D99, NS-6 slice 6c): which tab a link opens,
 * which chat's files show, and how the assistant links here.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { chatFilesHref } from "@/lib/shell/chatHistory";

import { artifactsTab, chatToShow, linkedFile } from "./chatFiles";

const SRC = fileURLToPath(new URL("../../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

const at = (min: number) => new Date(Date.parse("2026-10-13T12:00:00Z") - min * 60_000).toISOString();
const chat = (id: string, over: Partial<{ updatedAt: string; messageCount: number; archivedAt: string | null }> = {}) => ({
  id,
  updatedAt: at(0),
  messageCount: 2,
  archivedAt: null,
  ...over,
});

describe("the tab", () => {
  it("opens on the chats, unless the link asks for the agents' files", () => {
    expect(artifactsTab(null)).toBe("chats");
    expect(artifactsTab("chats")).toBe("chats");
    expect(artifactsTab("agents")).toBe("agents");
    expect(artifactsTab("anything")).toBe("chats");
  });
});

describe("the chat whose files show", () => {
  const sessions = [chat("old", { updatedAt: at(60) }), chat("new", { updatedAt: at(1) })];

  it("the chat the link or the member picked", () => {
    expect(chatToShow(sessions, "old")).toBe("old");
  });

  it("a chat the member does not hold falls back to the newest", () => {
    expect(chatToShow(sessions, "someone-elses")).toBe("new");
  });

  it("with nothing picked, the newest chat with a turn that is not archived", () => {
    const more = [
      ...sessions,
      chat("newer-but-empty", { updatedAt: at(0), messageCount: 0 }),
      chat("newer-but-archived", { updatedAt: at(0), archivedAt: at(0) }),
    ];
    expect(chatToShow(more, null)).toBe("new");
  });

  it("no chat at all shows nothing", () => {
    expect(chatToShow([], null)).toBeNull();
  });
});

describe("the assistant links a chat's files here", () => {
  it("the address names the chat", () => {
    expect(chatFilesHref("a b/c")).toBe("/artifacts?session=a%20b%2Fc");
  });

  it("the side panel's Files button opens Artifacts, and the full view keeps its own column", () => {
    const dock = read("lib/shell/AssistantDock.tsx");
    expect(dock).toContain("router.push(chatFilesHref(chat.activeId));");
    expect(dock).toContain('aria-label="Open this chat\'s files in Artifacts"');
    expect(dock).toContain("onClick={() => setFilesOpen((o) => !o)}");
  });

  it("the page reads the chat from the address, and lists files through the per-chat route", () => {
    const page = read("app/artifacts/page.tsx");
    expect(page).toContain('const asked = params.get("session");');
    expect(page).toMatch(/<ArtifactSidebar[\s\S]{0,120}sessionId=\{chat\.id\}/);
    expect(page).toContain("<ArtifactViewerModal sessionId={chat.id}");
  });

  it("the page opens with the one app bar, and the old explorer is a tab with no h1 of its own", () => {
    const page = read("app/artifacts/page.tsx");
    expect(page).toContain('<AppTopBar title="Artifacts"');
    expect(page).not.toMatch(/<h1\b/);
    expect(page).toContain("function AgentWorkspaces()");
  });
});

describe("linkedFile — `?file=` opens one file in the viewer (2026-10-16)", () => {
  it("names the file of the link, in the session of the link", () => {
    expect(linkedFile("s-1", "outputs/report.md")).toEqual({
      sessionId: "s-1", path: "outputs/report.md", name: "report.md",
    });
  });

  it("opens nothing without both values, or for an odd session id", () => {
    expect(linkedFile("s-1", null)).toBeNull();
    expect(linkedFile(null, "outputs/a.md")).toBeNull();
    expect(linkedFile("a/b", "outputs/a.md")).toBeNull();
    expect(linkedFile("a.b", "outputs/a.md")).toBeNull();
  });

  it("the page opens it in the one viewer", () => {
    const page = readFileSync(join(SRC, "app", "artifacts", "page.tsx"), "utf-8");
    expect(page).toContain("linkedFile(asked, askedFile)");
    expect(page).toContain("entry={linkedFileEntry(linked)}");
  });
});
