/**
 * The one assistant's session picker (D99), shaped as Claude Code's: one
 * list newest first with a short age, and the archived chats apart.
 */
import { describe, expect, it } from "vitest";

import type { ChatSession } from "@/lib/sessions";

import { matchesQuery, mergeFileEntry, relativeAge, sessionTitle, splitSessions } from "./chatHistory";

const NOW = Date.parse("2026-10-12T12:00:00Z");
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const MIN = 60_000;

function chat(id: string, over: Partial<ChatSession> = {}): ChatSession {
  return {
    id,
    name: id,
    agentName: "orchestrator",
    createdAt: ago(0),
    updatedAt: ago(0),
    messageCount: 2,
    title: `Chat ${id}`,
    ...over,
  };
}

describe("a chat's age", () => {
  it("reads now, minutes, hours, days and weeks", () => {
    expect(relativeAge(ago(30_000), NOW)).toBe("now");
    expect(relativeAge(ago(4 * MIN), NOW)).toBe("4m");
    expect(relativeAge(ago(3 * 60 * MIN), NOW)).toBe("3h");
    expect(relativeAge(ago(26 * 60 * MIN), NOW)).toBe("1d");
    expect(relativeAge(ago(15 * 24 * 60 * MIN), NOW)).toBe("2w");
  });

  it("a clock that runs fast reads now, and a bad time reads nothing", () => {
    expect(relativeAge(new Date(NOW + 5 * MIN).toISOString(), NOW)).toBe("now");
    expect(relativeAge("not a date", NOW)).toBe("");
  });

  it("from eight weeks it is the date", () => {
    expect(relativeAge(ago(70 * 24 * 60 * MIN), NOW)).toMatch(/Aug/);
  });
});

describe("the two lists", () => {
  it("newest first, with the archived chats apart", () => {
    const lists = splitSessions([
      chat("old", { updatedAt: ago(60 * MIN) }),
      chat("new", { updatedAt: ago(MIN) }),
      chat("put-away", { archivedAt: ago(5 * MIN) }),
    ]);
    expect(lists.open.map((s) => s.id)).toEqual(["new", "old"]);
    expect(lists.archived.map((s) => s.id)).toEqual(["put-away"]);
  });

  it("drops an empty chat, unless it is the one on screen", () => {
    const sessions = [chat("empty", { messageCount: 0 }), chat("real")];
    expect(splitSessions(sessions).open.map((s) => s.id)).toEqual(["real"]);
    expect(splitSessions(sessions, "", "empty").open.map((s) => s.id)).toContain("empty");
  });

  it("keeps a chat of any agent, so an old specialist chat still opens", () => {
    expect(splitSessions([chat("s", { agentName: "projects-assistant" })]).open).toHaveLength(1);
  });

  it("the search reads the name and the last reply, in any case", () => {
    const s = chat("q", { title: "Zoho mirror", lastPreview: "The deal stages synced" });
    expect(matchesQuery(s, "zoho")).toBe(true);
    expect(matchesQuery(s, "STAGES")).toBe(true);
    expect(matchesQuery(s, "invoice")).toBe(false);
    expect(splitSessions([s, chat("other")], "zoho").open.map((x) => x.id)).toEqual(["q"]);
  });

  it("a chat with no name reads New chat", () => {
    expect(sessionTitle({ title: "   " })).toBe("New chat");
    expect(sessionTitle({ title: undefined })).toBe("New chat");
  });
});

describe("a chat's files", () => {
  it("one row per path, and a later event keeps the size it does not send", () => {
    let files = mergeFileEntry([], { path: "out/t-1/report.md", size: 120, mimeType: "text/markdown" }, "t1");
    files = mergeFileEntry(files, { path: "out/t-1/report.md" }, "t2");
    expect(files).toEqual([
      { path: "out/t-1/report.md", name: "report.md", size: 120, modified_at: "t2", mime_type: "text/markdown" },
    ]);
  });

  it("an event with no path changes nothing", () => {
    expect(mergeFileEntry([], { path: "" }, "t")).toEqual([]);
  });
});
