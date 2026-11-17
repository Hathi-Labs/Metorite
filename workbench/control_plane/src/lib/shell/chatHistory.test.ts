/**
 * The one assistant's session picker (D99), shaped as Claude Code's: one
 * list newest first with a short age, and the archived chats apart.
 */
import { describe, expect, it } from "vitest";

import type { ChatSession } from "@/lib/sessions";

import {
  OTHER_GROUP,
  PINNED_GROUP,
  RECENT_GROUP,
  chatGroups,
  dateGroups,
  groupKey,
  isPinned,
  matchesQuery,
  mergeFileEntry,
  pickerGroups,
  relativeAge,
  rowKey,
  sessionTitle,
  shownTopic,
  splitSessions,
  topicGroups,
  walkOrder,
} from "./chatHistory";

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

describe("the chat list by day (CO-0, owner 2026-11-02)", () => {
  const H = 60 * 60 * 1000;
  // A fixed local noon, so "today" and "yesterday" do not hang on the zone.
  const noon = new Date(2026, 10, 2, 12, 0, 0).getTime();
  const at = (ms: number) => ({ updatedAt: new Date(noon - ms).toISOString() });

  it("puts each chat in its day group, newest group first, and leaves out an empty group", () => {
    const groups = dateGroups([at(1 * H), at(20 * H), at(3 * 24 * H), at(40 * 24 * H), at(2 * H)], noon);
    expect(groups.map((g) => [g.label, g.items.length])).toEqual([
      ["Today", 2],
      ["Yesterday", 1],
      ["Previous 7 days", 1],
      ["Older", 1],
    ]);
  });

  it("keeps the order it was given inside a group", () => {
    const a = at(1 * H), b = at(2 * H);
    expect(dateGroups([a, b], noon)[0].items).toEqual([a, b]);
  });

  it("puts a chat with no date in Older", () => {
    expect(dateGroups([{ updatedAt: "" }], noon)[0].label).toBe("Older");
  });
});

describe("the Pinned group (CO-11, chat_organization.md §18)", () => {
  const H = 60 * 60 * 1000;
  const noon = new Date(2026, 10, 16, 12, 0, 0).getTime();
  const iso = (ms: number) => new Date(noon - ms).toISOString();
  const row = (id: string, over: Partial<ChatSession> = {}) =>
    chat(id, { updatedAt: iso(1 * H), ...over });

  it("puts the pinned chats first, newest pin first, and each once", () => {
    const groups = chatGroups(
      [
        row("today"),
        row("old-pin", { updatedAt: iso(40 * 24 * H), pinnedAt: iso(5 * H) }),
        row("new-pin", { updatedAt: iso(3 * 24 * H), pinnedAt: iso(1 * H) }),
        row("yesterday", { updatedAt: iso(20 * H) }),
      ],
      noon,
      true,
    );
    expect(groups.map((g) => [g.label, g.items.map((s) => s.id)])).toEqual([
      [PINNED_GROUP, ["new-pin", "old-pin"]],
      ["Today", ["today"]],
      ["Yesterday", ["yesterday"]],
    ]);
  });

  it("an archived chat is not pinned, so it never shows in Pinned", () => {
    const put = row("put-away", { pinnedAt: iso(1 * H), archivedAt: iso(0) });
    expect(isPinned(put)).toBe(false);
    expect(isPinned(row("kept", { pinnedAt: iso(1 * H) }))).toBe(true);
    const groups = chatGroups([put, row("today")], noon, true);
    expect(groups.map((g) => g.label)).not.toContain(PINNED_GROUP);
  });

  it("with the switch off there is no Pinned group, and the day groups are as before", () => {
    const sessions = [row("a", { pinnedAt: iso(1 * H) }), row("b", { updatedAt: iso(20 * H) })];
    expect(chatGroups(sessions, noon, false)).toEqual(dateGroups(sessions, noon));
    expect(chatGroups(sessions, noon, false).map((g) => g.label)).not.toContain(PINNED_GROUP);
  });

  it("no pinned chat, no Pinned group", () => {
    expect(chatGroups([row("a"), row("b")], noon, true).map((g) => g.label)).toEqual(["Today"]);
  });
});

// Mutations, each red here: drop the `topicsOn` return of `topicGroups` (the
// switch-off case); key a group on its name and not its id (two topics of
// one name).
describe("the topic groups (CO-2b, chat_organization.md §6)", () => {
  const H = 60 * 60 * 1000;
  const noon = new Date(2026, 10, 17, 12, 0, 0).getTime();
  const iso = (ms: number) => new Date(noon - ms).toISOString();
  const row = (id: string, over: Partial<ChatSession> = {}) =>
    chat(id, { updatedAt: iso(1 * H), ...over });
  const sales = { topicId: "t-sales", topicName: "Sales" };
  const board = { topicId: "t-board", topicName: "board prep" };
  const sessions = [
    row("loose-today"),
    row("sales-old", { ...sales, updatedAt: iso(3 * 24 * H) }),
    row("pinned-sales", { ...sales, pinnedAt: iso(2 * H) }),
    row("board", { ...board, updatedAt: iso(20 * H) }),
    row("sales-new", { ...sales, updatedAt: iso(0.5 * H) }),
    row("loose-yesterday", { updatedAt: iso(20 * H) }),
  ];
  const shape = (groups: { label: string; topicId?: string; items: ChatSession[] }[]) =>
    groups.map((g) => [g.label, g.topicId ?? null, g.items.map((s) => s.id)]);

  it("with the switch off it is chatGroups, with the pins on and off", () => {
    for (const pinsOn of [true, false]) {
      expect(topicGroups(sessions, noon, false, pinsOn)).toEqual(chatGroups(sessions, noon, pinsOn));
    }
  });

  it("orders Recent, Pinned, then the groups by their newest chat, with Other among them", () => {
    expect(shape(topicGroups(sessions, noon, true, true))).toEqual([
      // A tie of time goes by id: loose-today before pinned-sales, board before loose-yesterday.
      [RECENT_GROUP, null, ["sales-new", "loose-today", "pinned-sales", "board", "loose-yesterday"]],
      [PINNED_GROUP, null, ["pinned-sales"]],
      ["Sales", "t-sales", ["sales-new", "sales-old"]],
      [OTHER_GROUP, null, ["loose-today", "loose-yesterday"]],
      ["board prep", "t-board", ["board"]],
    ]);
  });

  it("with the pins off a pinned chat sits in its topic", () => {
    const groups = topicGroups(sessions, noon, true, false);
    expect(groups.map((g) => g.label)).not.toContain(PINNED_GROUP);
    expect(groups[0].label).toBe(RECENT_GROUP);
    expect(groups.find((g) => g.topicId === "t-sales")?.items.map((s) => s.id)).toEqual([
      "sales-new",
      "pinned-sales",
      "sales-old",
    ]);
  });

  it("keys a group on the topic id: two topics of one name stay two groups", () => {
    const mine = row("mine", { topicId: "t-mine", topicName: "Sales" });
    const groups = topicGroups([mine, row("co", { ...sales })], noon, true, true).filter((g) => !g.recent);
    // One time, so the tie goes by the group key.
    expect(groups.map((g) => g.topicId)).toEqual(["t-mine", "t-sales"]);
    expect(groups.map((g) => g.label)).toEqual(["Sales", "Sales"]);
  });

  it("a topic called Today is a topic group, and no day group shows", () => {
    const today = row("odd", { topicId: "t-today", topicName: "Today" });
    const loose = row("loose", { updatedAt: iso(2 * H) });
    expect(shape(topicGroups([today, loose], noon, true, true)).slice(1)).toEqual([
      ["Today", "t-today", ["odd"]],
      [OTHER_GROUP, null, ["loose"]],
    ]);
  });

  it("a chat with no topic, or a topic the server did not name, sits in Other", () => {
    // The server sends null for a hidden topic. An id with no name shows none.
    const hidden = row("hidden", { topicId: null, topicName: null });
    const nameless = row("nameless", { topicId: "t-x", topicName: "  " });
    expect(shownTopic(hidden)).toBeNull();
    expect(shownTopic(nameless)).toBeNull();
    expect(shape(topicGroups([hidden, nameless], noon, true, true))).toEqual([
      [RECENT_GROUP, null, ["hidden", "nameless"]],
      [OTHER_GROUP, null, ["hidden", "nameless"]],
    ]);
  });

  it("an archived pinned chat is not in Pinned, and goes to its topic", () => {
    const put = row("put-away", { ...sales, pinnedAt: iso(1 * H), archivedAt: iso(0) });
    expect(shape(topicGroups([put], noon, true, true))).toEqual([
      [RECENT_GROUP, null, ["put-away"]],
      ["Sales", "t-sales", ["put-away"]],
    ]);
  });
});

// CO-2c, the order of the list. Mutations, each red here: take six chats in
// Recent and not five (`RECENT_COUNT`); sort Recent oldest first; drop the id
// tie-break of `newestFirst`; sort the groups by name again; leave a pinned
// chat out of Recent; make Recent in a search (`pickerGroups`); let the
// switch-off case build Recent; draw the day groups of the chats with no
// topic; drop the `recent:` prefix of `rowKey`.
describe("the order of the list (CO-2c, chat_organization.md §6.1)", () => {
  const H = 60 * 60 * 1000;
  const noon = new Date(2026, 10, 17, 12, 0, 0).getTime();
  const iso = (ms: number) => new Date(noon - ms).toISOString();
  const at = (id: string, hoursAgo: number, over: Partial<ChatSession> = {}) =>
    chat(id, { updatedAt: iso(hoursAgo * H), ...over });
  const ids = (g: { items: { id: string }[] }) => g.items.map((s) => s.id);
  const eight = [
    at("c1", 8, { topicId: "t-a", topicName: "Alpha" }),
    at("c2", 1),
    at("c3", 7),
    at("c4", 2, { topicId: "t-b", topicName: "Beta" }),
    at("c5", 6),
    at("c6", 3),
    at("c7", 5, { topicId: "t-a", topicName: "Alpha" }),
    at("c8", 4),
  ];

  it("Recent holds the five newest chats, newest first, out of eight", () => {
    const [recent] = topicGroups(eight, noon, true, true);
    expect(recent.label).toBe(RECENT_GROUP);
    expect(ids(recent)).toEqual(["c2", "c4", "c6", "c8", "c7"]);
  });

  it("a tie of time goes by id", () => {
    const tied = [at("b", 1), at("a", 1), at("c", 1)];
    expect(ids(topicGroups(tied, noon, true, true)[0])).toEqual(["a", "b", "c"]);
    expect(ids(topicGroups([...tied].reverse(), noon, true, true)[0])).toEqual(["a", "b", "c"]);
  });

  it("a pinned chat is in Recent and in Pinned, and in no third place", () => {
    const list = [at("p", 1, { pinnedAt: iso(9 * H), topicId: "t-a", topicName: "Alpha" }), at("q", 2)];
    const groups = topicGroups(list, noon, true, true);
    const holding = groups.filter((g) => ids(g).includes("p")).map((g) => g.label);
    expect(holding).toEqual([RECENT_GROUP, PINNED_GROUP]);
  });

  it("a chat with no topic is in Recent and in Other, and a topic chat in Recent and its topic", () => {
    const groups = topicGroups([at("x", 1), at("y", 2, { topicId: "t-a", topicName: "Alpha" })], noon, true, true);
    expect(groups.filter((g) => ids(g).includes("x")).map((g) => g.label)).toEqual([RECENT_GROUP, OTHER_GROUP]);
    expect(groups.filter((g) => ids(g).includes("y")).map((g) => g.label)).toEqual([RECENT_GROUP, "Alpha"]);
    // No day group shows with topics on.
    expect(groups.map((g) => g.label)).not.toContain("Today");
  });

  it("with fewer than five chats Recent holds them all", () => {
    expect(ids(topicGroups([at("a", 2), at("b", 1)], noon, true, true)[0])).toEqual(["b", "a"]);
  });

  it("the groups go by their newest chat, and each holds its chats newest first", () => {
    const groups = topicGroups(eight, noon, true, true).slice(1);
    // Beta: c4 at 2h. Other: c2 at 1h. Alpha: c7 at 5h.
    expect(groups.map((g) => g.label)).toEqual([OTHER_GROUP, "Beta", "Alpha"]);
    expect(ids(groups[0])).toEqual(["c2", "c6", "c8", "c5", "c3"]);
    expect(ids(groups[2])).toEqual(["c7", "c1"]);
  });

  it("a chat in a hidden topic reads as no topic and sits in Other", () => {
    const hidden = at("h", 1, { topicId: "t-gone", topicName: null });
    const groups = topicGroups([hidden], noon, true, true);
    expect(groups.map((g) => g.label)).toEqual([RECENT_GROUP, OTHER_GROUP]);
  });

  it("an empty list has no group", () => {
    expect(topicGroups([], noon, true, true)).toEqual([]);
  });

  it("a search draws no Recent: the picker gets null and draws one flat list", () => {
    expect(pickerGroups(eight, "chat", noon, true, true)).toBeNull();
    expect(pickerGroups(eight, "", noon, true, true)?.[0].label).toBe(RECENT_GROUP);
  });

  it("with the topic switch off the list is exactly chatGroups, with the pins on and off", () => {
    for (const pinsOn of [true, false]) {
      const list = [...eight, at("pp", 1, { pinnedAt: iso(H) })];
      expect(topicGroups(list, noon, false, pinsOn)).toEqual(chatGroups(list, noon, pinsOn));
      expect(pickerGroups(list, "", noon, false, pinsOn)).toEqual(chatGroups(list, noon, pinsOn));
      expect(topicGroups(list, noon, false, pinsOn).map((g) => g.label)).not.toContain(RECENT_GROUP);
    }
  });

  it("the copy in Recent has its own key and id, and each row shows once in the walk", () => {
    const list = [at("p", 1, { pinnedAt: iso(9 * H) }), at("q", 2, { topicId: "t-a", topicName: "Alpha" })];
    const groups = topicGroups(list, noon, true, true);
    const walk = walkOrder(groups);
    // Recent copies first, then the copy in each group. No id repeats.
    expect(walk).toEqual(["recent:p", "recent:q", "p", "q"]);
    expect(new Set(walk).size).toBe(walk.length);
    expect(groups.map((g) => groupKey(g))).toEqual(["recent", "Pinned", "topic:t-a"]);
    expect(rowKey(groups[0], "p")).toBe("recent:p");
    expect(rowKey(groups[1], "p")).toBe("p");
  });
});
