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

import {
  COMPANY_HEADING,
  CREATE_FAILED,
  MINE_HEADING,
  NO_TOPIC,
  NO_TOPICS_YET,
  TOPICS_DID_NOT_LOAD,
  TOPICS_PATH,
  chipLabel,
  createChatTopic,
  createProblem,
  fetchChatTopics,
  parseTopics,
  topicMenuRows,
  type ChatTopic,
} from "./chatTopics";

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
  it("lists company topics, then mine, then No topic and New topic", () => {
    const rows = topicMenuRows([SALES, BOARD, FINANCE], "t-board");
    expect(rows).toEqual([
      { kind: "heading", label: COMPANY_HEADING },
      { kind: "topic", topic: { id: "t-sales", name: "Sales" }, current: false },
      { kind: "topic", topic: { id: "t-fin", name: "Finance" }, current: false },
      { kind: "heading", label: MINE_HEADING },
      { kind: "topic", topic: { id: "t-board", name: "Board prep" }, current: true },
      { kind: "none", current: false },
      { kind: "new" },
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
    const empty = [{ kind: "empty" }, { kind: "none", current: true }, { kind: "new" }];
    expect(topicMenuRows([], null)).toEqual(empty);
    // A list of hidden topics only is empty for the member.
    expect(topicMenuRows([{ ...SALES, hidden: true }], null)).toEqual(empty);
    expect(NO_TOPICS_YET).toMatch(/^No topics yet\./);
  });

  it("says that the list loads before the read answers, and still offers No topic", () => {
    expect(topicMenuRows(undefined, "t-sales")).toEqual([
      { kind: "loading" },
      { kind: "none", current: false },
      { kind: "new" },
    ]);
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
