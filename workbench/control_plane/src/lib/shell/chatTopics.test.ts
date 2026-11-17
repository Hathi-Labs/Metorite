/**
 * The topic chip of a chat (WS-CO CO-2b, `chat_organization.md` §6, D163).
 *
 * The chip shows the topic of the chat, or "No topic". Its menu lists the
 * company topics, then the member's own topics, then "No topic" and "New
 * topic…". A hidden topic never shows. Fence (R7): this file. Mutations,
 * each red here: drop the `hidden` filter of `parseTopics`; mark no row as
 * current; send the create with a body that names the member.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { peek, put } from "@/lib/dataCache";

import {
  COMPANY_HEADING,
  CREATE_FAILED,
  FIXED_ROWS,
  MINE_HEADING,
  MOVE_FAILED,
  MOVE_FAILED_NEXT,
  NO_TOPIC,
  NO_TOPICS_YET,
  TOPICS_DID_NOT_LOAD,
  TOPICS_PATH,
  chipLabel,
  createChatTopic,
  createProblem,
  fetchChatTopics,
  focusAfterLoad,
  menuFocusIndex,
  parseTopics,
  topicMenuIsOpen,
  topicMenuRows,
  topicMoveFailed,
  type ChatTopic,
} from "./chatTopics";

const source = (name: string) => readFileSync(fileURLToPath(new URL(`./${name}`, import.meta.url)), "utf-8");

const SALES: ChatTopic = { id: "t-sales", name: "Sales", scope: "company", hidden: false };
const FINANCE: ChatTopic = { id: "t-fin", name: "Finance", scope: "company", hidden: false };
const BOARD: ChatTopic = { id: "t-board", name: "Board prep", scope: "member", hidden: false };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the words of the chip", () => {
  it("shows the topic of the chat, or No topic", () => {
    expect(chipLabel({ topicId: "t-sales", topicName: "Sales" })).toBe("Sales");
    expect(chipLabel({ topicId: null, topicName: null })).toBe(NO_TOPIC);
    expect(chipLabel({})).toBe(NO_TOPIC);
    expect(chipLabel(null)).toBe(NO_TOPIC);
    // An id with no name is no topic the chip can show.
    expect(chipLabel({ topicId: "t-x", topicName: "" })).toBe(NO_TOPIC);
  });
});

describe("the topics of the answer", () => {
  it("keeps company and own topics, and drops a hidden topic and a bad row", () => {
    const body = {
      topics: [
        { id: "t-sales", name: "Sales", scope: "company", mine: false, hidden: false },
        { id: "t-legal", name: "Legal", scope: "company", mine: false, hidden: true },
        { id: "t-board", name: "Board prep", scope: "member", mine: true, hidden: false },
        { id: 7, name: "Bad", scope: "company" },
        { id: "t-odd", name: "Odd", scope: "team" },
        null,
      ],
      can_manage: false,
    };
    expect(parseTopics(body)).toEqual([SALES, BOARD]);
    expect(parseTopics(null)).toEqual([]);
    expect(parseTopics({ topics: "no" })).toEqual([]);
  });
});

describe("the rows of the menu", () => {
  it("puts New topic and No topic first, then company topics, then mine", () => {
    const rows = topicMenuRows([SALES, BOARD, FINANCE], "t-board");
    expect(rows).toEqual([
      { kind: "new" },
      { kind: "none", current: false },
      { kind: "heading", label: COMPANY_HEADING },
      { kind: "topic", topic: { id: "t-sales", name: "Sales" }, current: false },
      { kind: "topic", topic: { id: "t-fin", name: "Finance" }, current: false },
      { kind: "heading", label: MINE_HEADING },
      { kind: "topic", topic: { id: "t-board", name: "Board prep" }, current: true },
    ]);
  });

  it("marks No topic as current for a chat with no topic", () => {
    const rows = topicMenuRows([SALES], null);
    expect(rows.filter((r) => "current" in r && r.current)).toEqual([{ kind: "none", current: true }]);
  });

  it("leaves out a heading with no topic under it", () => {
    expect(topicMenuRows([BOARD], null).filter((r) => r.kind === "heading")).toEqual([
      { kind: "heading", label: MINE_HEADING },
    ]);
  });

  it("says that the list is empty, and offers New topic as the next act (DESIGN_SYSTEM §10)", () => {
    const empty = [{ kind: "new" }, { kind: "none", current: true }, { kind: "empty" }];
    expect(topicMenuRows([], null)).toEqual(empty);
    // A list of hidden topics only is empty for the member.
    expect(topicMenuRows([{ ...SALES, hidden: true }], null)).toEqual(empty);
    expect(NO_TOPICS_YET).toMatch(/^No topics yet\./);
  });

  it("says that the list loads before the read answers, and still offers No topic", () => {
    expect(topicMenuRows(undefined, "t-sales")).toEqual([
      { kind: "new" },
      { kind: "none", current: false },
      { kind: "loading" },
    ]);
  });

  // Review P2-3. Mutation: put the list before the fixed rows again -> red.
  it("keeps the fixed rows at one place while the list loads, is empty, or holds topics", () => {
    const states = [
      topicMenuRows(undefined, "t-sales"),
      topicMenuRows([], "t-sales"),
      topicMenuRows([SALES, BOARD, FINANCE], "t-sales"),
    ];
    for (const rows of states) {
      expect(rows.slice(0, FIXED_ROWS).map((r) => r.kind)).toEqual(["new", "none"]);
    }
  });

  // Review P2-3. Mutation: focus the first row with `current`, "No topic" too -> red.
  it("gives the first focus to the current topic, else New topic, and never No topic", () => {
    const loading = topicMenuRows(undefined, "t-sales");
    expect(loading[menuFocusIndex(loading)].kind).toBe("new");
    const loaded = topicMenuRows([SALES, BOARD], "t-board");
    expect(loaded[menuFocusIndex(loaded)]).toEqual({
      kind: "topic", topic: { id: "t-board", name: "Board prep" }, current: true,
    });
    // A chat with no topic: "No topic" is the current row, and it takes no focus.
    const none = topicMenuRows([SALES], null);
    expect(none[menuFocusIndex(none)].kind).toBe("new");
    for (const rows of [loading, loaded, none, topicMenuRows([], null)]) {
      expect(rows[menuFocusIndex(rows)].kind).not.toBe("none");
    }
  });

  it("never offers a hidden topic, even when the chat sits in it", () => {
    const legal: ChatTopic = { id: "t-legal", name: "Legal", scope: "company", hidden: true };
    const rows = topicMenuRows([legal, SALES], "t-legal");
    expect(rows.some((r) => r.kind === "topic" && r.topic.id === "t-legal")).toBe(false);
  });
});

describe("the requests", () => {
  it("reads the list with no cache, and throws on a refusal", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ topics: [SALES] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await fetchChatTopics()).toEqual([SALES]);
    expect(fetchMock.mock.calls[0]).toEqual([TOPICS_PATH, { cache: "no-store" }]);
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 404 })));
    await expect(fetchChatTopics()).rejects.toThrow("404");
  });

  it("makes a topic with its name only, and gives the topic", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ topic: { ...BOARD, mine: true } }), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await createChatTopic("Board prep")).toEqual({ topic: { id: "t-board", name: "Board prep" } });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(TOPICS_PATH);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ name: "Board prep" });
  });

  it("gives the words of the server for a duplicate or a bad name, and plain words else", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: "You already have a topic with this name." }), { status: 409 })));
    expect(await createChatTopic("Board prep")).toEqual({ problem: "You already have a topic with this name." });
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));
    expect(await createChatTopic("Board prep")).toEqual({ problem: CREATE_FAILED });
    // A 422 of the model sends a list, never words for the member.
    expect(createProblem(422, { detail: [{ msg: "x" }] })).toBe(CREATE_FAILED);
    expect(createProblem(500, { detail: "boom" })).toBe(CREATE_FAILED);
    // Each failure says what happened and what to do next (DESIGN_SYSTEM §10).
    for (const words of [CREATE_FAILED, TOPICS_DID_NOT_LOAD]) expect(words).toMatch(/try again\.$/);
  });
});

// Review P2-2. Mutation: drop the `invalidate` of `topicMoveFailed` -> the
// cached list stays; drop the toast -> nothing says why.
describe("a move that failed", () => {
  it("says what happened and the next step, and reads the topics again", () => {
    put(TOPICS_PATH, [SALES]);
    expect(peek(TOPICS_PATH)).toBeDefined();
    const shown: unknown[] = [];
    topicMoveFailed({ show: (spec) => (shown.push(spec), "id") }, "s-1");
    expect(shown).toEqual([
      { key: "chat-topic:s-1", variant: "error", title: MOVE_FAILED, description: MOVE_FAILED_NEXT },
    ]);
    expect(`${MOVE_FAILED} ${MOVE_FAILED_NEXT}`).toBe("The chat did not move. Try again, or pick another topic.");
    expect(peek(TOPICS_PATH)).toBeUndefined();
  });
});

// Review P2-1. The topic menu of the chat-first phone opens FOR one chat.
// Mutation: keep a plain boolean open state -> the menu shows on a new chat.
describe("the topic menu and a change of chat", () => {
  it("is open only for the chat it opened for, and a change of chat closes it", () => {
    expect(topicMenuIsOpen("chat-a", "chat-a")).toBe(true);
    expect(topicMenuIsOpen("chat-a", "chat-b")).toBe(false);
    expect(topicMenuIsOpen(null, "chat-a")).toBe(false);
    expect(topicMenuIsOpen("chat-a", null)).toBe(false);
    expect(topicMenuIsOpen("chat-a", undefined)).toBe(false);
  });

  it("the dock forgets the open chat on each change, and the menu takes focus only while open", () => {
    const dock = source("AssistantDock.tsx");
    expect(dock).toMatch(/useEffect\(\(\) => \{\s+setTopicMenuFor\(null\);\s+\}, \[activeChatId\]\);/);
    expect(dock).toContain("const topicMenuOpen = topicMenuIsOpen(topicMenuFor, active?.id);");
    // The menu hangs from the "…" control of this panel, never from a header.
    expect(dock).toContain('setTopicAnchor(rootRef.current?.querySelector<HTMLElement>("[data-chat-more]") ?? null);');
    expect(dock).not.toContain("setHeaderEl");
    const chip = source("TopicChip.tsx");
    // The focus effect returns at once while the menu is closed.
    expect(chip).toMatch(/useEffect\(\(\) => \{\s+if \(!open \|\| naming\) return;/);
    // Escape gives the focus back to the trigger: the "…" button itself.
    expect(chip).toContain('anchor?.tagName === "BUTTON" ? anchor : anchor?.querySelector<HTMLElement>("button")');
  });

  // Visual review P2: a cold open. Mutation: drop `onFirstRow` from
  // `focusAfterLoad` -> the focus leaves a row the member moved to.
  it("moves the focus once to the current topic when the list lands, and never after the member moved it", () => {
    // Cold open: the first focus sat on "New topic…", the member did nothing.
    expect(focusAfterLoad({ reran: false, onFirstRow: true, moves: true })).toBe(true);
    // The member moved the focus (or typed in the name form): leave it.
    expect(focusAfterLoad({ reran: false, onFirstRow: false, moves: true })).toBe(false);
    // Once only: a second landing of the list moves nothing.
    expect(focusAfterLoad({ reran: true, onFirstRow: true, moves: true })).toBe(false);
    // A warm open: the first focus is already the current topic.
    expect(focusAfterLoad({ reran: false, onFirstRow: true, moves: false })).toBe(false);
    // The target of a cold open is the current topic once the list holds it.
    const cold = topicMenuRows(undefined, "t-board");
    const warm = topicMenuRows([SALES, BOARD], "t-board");
    expect(cold[menuFocusIndex(cold)].kind).toBe("new");
    expect(warm[menuFocusIndex(warm)]).toMatchObject({ kind: "topic", current: true });
  });

  // Mutation: drop the effect that runs on `loaded` -> red here.
  it("the chip runs the focus again when the list lands, once, and only on the first row", () => {
    const chip = source("TopicChip.tsx");
    expect(chip).toContain("const loaded = topics.data !== undefined;");
    expect(chip).toMatch(/if \(!open \|\| naming \|\| !loaded\) return;/);
    expect(chip).toContain("const onFirstRow = first.el !== null && document.activeElement === first.el;");
    expect(chip).toMatch(/focusAfterLoad\(\{ reran: first\.reran, onFirstRow,/);
    expect(chip).toContain("first.reran = true;");
    expect(chip).toContain("}, [open, naming, loaded]);");
    // Each open starts again: the first focus records its row.
    expect(chip).toContain("firstFocus.current = { el: null, reran: false };");
    expect(chip).toContain("firstFocus.current.el = el;");
  });

  // Visual review P3. Mutation: give the menu heading its own size again.
  it("the menu headings have the type of the group headings of the chat list", () => {
    // The classes of each heading, less the padding, which follows the place.
    const typeOf = (classes: string) =>
      classes.split(/\s+/).filter((c) => c && !/^(glass:)?p[xytb]?-/.test(c)).sort();
    const list = /<h3 className="([^"]+)">\{g\.label\}<\/h3>/.exec(source("SessionPicker.tsx"));
    const menu = /function Heading[\s\S]*?<div className="([^"]+)">/.exec(source("TopicChip.tsx"));
    expect(list && menu).toBeTruthy();
    expect(typeOf(menu![1])).toEqual(typeOf(list![1]));
    expect(typeOf(list![1])).toContain("text-[11px]");
    expect(source("TopicChip.tsx")).not.toContain("text-[10px]");
  });

  it("the chip does not show on an archived chat, as the pin does not", () => {
    expect(source("AssistantDock.tsx")).toContain(
      "const topicable = topicsOn && active != null && active.messageCount > 0 && !active.archivedAt;",
    );
  });
});
