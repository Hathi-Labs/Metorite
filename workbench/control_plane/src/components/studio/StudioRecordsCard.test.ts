/**
 * The live records card of a Studio read, drawn in the chat (STU-F5a,
 * `project-docs/specs/ai_studio.md` §14 row F5a).
 *
 * `vitest.config.ts` runs in `node` with no DOM, so no effect runs. `mount`
 * below runs the reads that the hooks of the card run, through the same
 * functions and the same cache keys: the package read, then the list read on
 * mount, on focus (a stale entry) and on refresh (forced). It then draws the
 * turn with `renderToStaticMarkup` over the cache that those reads filled. The
 * pins in "the wiring" hold the hooks of the card to those functions.
 *
 * Mutations, each one red here (2026-11-13):
 * - drop `readActions` in `cardDoor`, so the door holds the write actions:
 *   "door.run of a write says not declared and calls no fetch";
 * - pass `{ editable: true }` in `recordsNode`: "the table does not edit";
 * - raise `MAX_RECORD_ROWS` to 30: "at most 25 rows";
 * - build the door with `createRealDoor` on the actions in the card:
 *   "the card builds its door through cardDoor only";
 * - drop the records mount in `StudioToolCards`: "the one MessageBubble
 *   mount draws it";
 * - drop the 404 branch of `readCardPackage`: "a 404 or Studio off draws no
 *   card and no error".
 *
 * Fix round 1 (review of 9ad018f2a):
 * - drop `invalidate(isLiveDoorKey)` in `announceAssistantWrite`: "a record
 *   write of Mira makes the card read its rows again" (P1);
 * - drop the 403 branch of `readCardPackage`: "a 403 draws no card and no
 *   error" (P2);
 * - drop the 401 branch of `readCardPackage`, or let `packageFailure` always
 *   offer a retry: "a 401 says to sign in, with no retry" (P2).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));

import MessageBubble from "@/components/MessageBubble";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { announceAssistantWrite } from "@/lib/assistantWrites";
import { clearAll, invalidate, peek, read as cacheRead, subscribe } from "@/lib/dataCache";
import { sourceKey } from "@/lib/studio/dataDoor";
import { NOT_DECLARED_SENTENCE, NOT_OPEN_SENTENCE, SIGN_IN_SENTENCE } from "@/lib/studio/realDoor";
import {
  LOAD_FAILED, MAX_RECORD_ROWS, cardDoor, emptyLine, packageFailure, packageKey, parsedArg, readCardPackage,
  recordsCall, recordsCalls, recordsNode, refusedResult, rowLimit, shownObject,
} from "@/lib/studio/recordsCard";

import { PackageFailedFrame, RecordsFrame } from "./StudioRecordsCard";
import StudioToolCards from "./StudioToolCards";

const PKG = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const SRC = fileURLToPath(new URL("../..", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../../..", import.meta.url));
const read = (abs: string) => readFileSync(abs, "utf8").replace(/\r\n/g, "\n");
/** Code and markup only: no comments. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

const OBJECT = {
  id: "0b1c2d3e-4f5a-4b6c-8d7e-9f0a1b2c3d4e",
  name: "visit",
  label: "Visit",
  fields: [
    { key: "title", type: "text", label: "Title", required: true, many: false, options: null, to: null, hot: false },
    { key: "status", type: "select", label: "Status", required: false, many: false,
      options: ["Planned", "Closed"], to: null, hot: false },
  ],
  rules: [],
};
/** The owner's manifest: each verb, and a declared action with a set. */
const ACTIONS = [
  { id: "find", uses: "object:visit.list" },
  { id: "peek", uses: "object:visit.get" },
  { id: "add", uses: "object:visit.create" },
  { id: "change", uses: "object:visit.update" },
  { id: "remove", uses: "object:visit.delete" },
  { id: "close_visit", uses: "object:visit.update", set: { status: "Closed" } },
];
const WRITE_IDS = ["add", "change", "remove", "close_visit"];

interface Call { method: string; url: string; body: unknown }

function records(prefix: string, n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `00000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
    data: { title: `${prefix}-${String(i + 1).padStart(2, "0")}`, status: "Planned" },
  }));
}

/**
 * A fake BFF. It answers the package read and the action route, and it
 * records every call. `rows` gives the records of one declared action id.
 */
function gateway(opts: {
  status?: number;
  pkg?: Record<string, unknown>;
  objects?: unknown[];
  actions?: unknown[];
  rows?: (actionId: string) => unknown[];
} = {}) {
  const calls: Call[] = [];
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  const impl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === `/api/studio/packages/${PKG}`) {
      if (opts.status) return json({ detail: "Not found" }, opts.status);
      return json({
        package: { id: PKG, name: "Field visits", manifest: { actions: opts.actions ?? ACTIONS }, ...(opts.pkg ?? {}) },
        objects: opts.objects ?? [OBJECT],
      });
    }
    const m = /^\/api\/studio\/apps\/[0-9a-f-]{36}\/actions\/([a-z0-9_]+)$/.exec(url);
    if (m && method === "POST") {
      const found = (opts.rows ?? (() => records("Site", 3)))(m[1]);
      return json({ records: found, total: found.length });
    }
    return json({ detail: "Not found" }, 404);
  });
  return { impl: impl as unknown as typeof fetch, calls, spy: impl };
}

function listEvent(args: Record<string, unknown>, id = "call-list-1", extra: Partial<ToolEvent> = {}): ToolEvent {
  return {
    id,
    name: "studio_record_list",
    args: { package_id: PKG, object: "visit", ...args },
    result: JSON.stringify({ ok: true, errors: [], diff: [], package_id: PKG, object: "visit", count: 3 }),
    status: "done",
    ...extra,
  };
}

/** The reads that the hooks of each card run, then the turn, drawn. */
async function mount(events: ToolEvent[], impl: typeof fetch): Promise<string> {
  vi.stubGlobal("fetch", impl);
  for (const call of recordsCalls(events)) {
    const pkg = await cacheRead(packageKey(call.packageId), () => readCardPackage(call.packageId));
    const object = shownObject(pkg, call.object);
    if (!pkg || !object) continue;
    const door = cardDoor(pkg);
    const key = sourceKey(door, call.source);
    await cacheRead(key, () => door.read(call.source)); // the mount
    invalidate(key);
    await cacheRead(key, () => door.read(call.source)); // a focus read of a stale entry
    await cacheRead(key, () => door.read(call.source), { force: true }); // refresh
  }
  return renderToStaticMarkup(createElement(StudioToolCards, { toolEvents: events, sessionId: "chat-1" }));
}

const cards = (html: string) => html.match(/data-studio-records="/g)?.length ?? 0;
const writes = (calls: Call[]) =>
  calls.filter((c) => c.method !== "GET" && !/\/actions\/(?:find|find_v1)$/.test(c.url));

afterEach(() => {
  clearAll();
  vi.unstubAllGlobals();
});

describe("one card for each read of the records, with live rows through the real door", () => {
  it("draws one card for each read, and the rows of each read", async () => {
    const g = gateway();
    const html = await mount([
      listEvent({}, "call-a"),
      listEvent({ filter: '[{"field":"status","op":"eq","value":"Planned"}]' }, "call-b"),
    ], g.impl);
    expect(cards(html)).toBe(2);
    expect(html).toContain(">Site-01<");
    expect(html).toContain("Field visits · Visit");
    // The rows came through the door of the card, from the declared list id.
    const posts = g.calls.filter((c) => c.method === "POST");
    expect(posts.length).toBeGreaterThan(0);
    for (const c of posts) expect(c.url).toBe(`/api/studio/apps/${PKG}/actions/find`);
  });

  it("a running call, a failed call, a refusal, a bad id and another tool draw no card", () => {
    const refusal = JSON.stringify({ ok: false, errors: ["No package of yours has this id."], diff: [] });
    const events: ToolEvent[] = [
      listEvent({}, "running", { status: "running", result: undefined }),
      listEvent({}, "failed", { status: "error" }),
      listEvent({}, "refused", { result: refusal }),
      listEvent({}, "refused-cut", { result: "{'ok': False, 'errors': ['The filter is a JSON li" }),
      listEvent({ package_id: "not-a-uuid" }, "bad-id"),
      listEvent({ object: "Visit; drop" }, "bad-object"),
      { ...listEvent({}, "other"), name: "studio_package_get" },
    ];
    expect(recordsCalls(events)).toEqual([]);
    expect(refusedResult(refusal)).toBe(true);
    expect(refusedResult("{\"ok\": true, \"records\": \"…cut")).toBe(false);
    expect(refusedResult(undefined)).toBe(false);
  });

  it("the same read twice in one turn draws one card, and a namespaced tool name still counts", () => {
    const calls = recordsCalls([
      listEvent({ sort: { field: "title", dir: "asc" } }, "one"),
      listEvent({ sort: '{"field":"title","dir":"asc"}' }, "two"),
      { ...listEvent({}, "three"), name: "studio__studio_record_list" },
    ]);
    expect(calls.map((c) => c.eventId)).toEqual(["one", "three"]);
  });
});

describe("the read: the filter and the sort of the call when they parse, and none when they do not", () => {
  it("sends the filter and the sort that parse, in the body of the list", async () => {
    const g = gateway();
    await mount([listEvent({
      filter: '[{"field":"status","op":"eq","value":"Planned"}]', sort: { field: "title", dir: "desc" },
    })], g.impl);
    const body = g.calls.find((c) => c.method === "POST")?.body;
    expect(body).toEqual({
      limit: MAX_RECORD_ROWS, filter: [{ field: "status", op: "eq", value: "Planned" }], sort: { field: "title", dir: "desc" },
    });
  });

  it("drops a filter or a sort that does not parse, and keeps the read", () => {
    const call = recordsCall(listEvent({ filter: "status is planned", sort: "[1]" }));
    expect(call?.source).toEqual({ action: "object:visit.list", args: { limit: MAX_RECORD_ROWS } });
    expect(call?.filtered).toBe(false);
    expect(parsedArg({ field: "x" }, "list")).toBeUndefined();
    expect(parsedArg([], "list")).toBeUndefined();
    expect(parsedArg("", "object")).toBeUndefined();
    expect(parsedArg({}, "object")).toBeUndefined();
  });

  it("never sends the offset of the call", () => {
    expect(recordsCall(listEvent({ offset: 25 }))?.source.args).toEqual({ limit: MAX_RECORD_ROWS });
  });

  it("says that nothing matches a filter, and that nothing exists without one", () => {
    expect(emptyLine({ filtered: true })).toBe("No records match this filter.");
    expect(emptyLine({ filtered: false })).toBe("No records yet.");
  });
});

describe("at most 25 rows", () => {
  it("equals the page of the tool", () => {
    const py = read(`${ROOT}/packages/acb_skills/acb_skills/studio_tools.py`);
    expect(py).toMatch(new RegExp(`^MAX_RECORD_ROWS = ${MAX_RECORD_ROWS}$`, "m"));
    expect(MAX_RECORD_ROWS).toBe(25);
  });

  it("the limit of the body is the limit of the call, at most 25", () => {
    expect(rowLimit(undefined)).toBe(25);
    expect(rowLimit(100)).toBe(25);
    expect(rowLimit(5)).toBe(5);
    expect(rowLimit("7")).toBe(7);
    for (const bad of [0, -3, 2.5, "x", null]) expect(rowLimit(bad)).toBe(25);
  });

  it("draws 25 rows when the store answers more, and says that more exist", async () => {
    const g = gateway({ rows: () => records("Site", 30) });
    const html = await mount([listEvent({ limit: 100 })], g.impl);
    expect(g.calls.find((c) => c.method === "POST")?.body).toEqual({ limit: 25 });
    expect(html).toContain(">Site-25<");
    expect(html).not.toContain(">Site-26<");
    expect(html).toContain("Showing the first 25 of at least 30 records.");
  });
});

describe("the door of the card writes nothing, for the owner too", () => {
  it("door.run of a write says not declared and calls no fetch", async () => {
    const g = gateway();
    // The owner's package: the manifest declares every write.
    const door = cardDoor({ id: PKG, actions: ACTIONS }, g.impl);
    for (const verb of ["create", "update", "delete"]) {
      expect(await door.run(`object:visit.${verb}`, { id: "x", data: { title: "New" } }))
        .toEqual({ ok: false, message: NOT_DECLARED_SENTENCE });
    }
    // A declared action id that writes is not open on this door either.
    for (const id of WRITE_IDS) expect(await door.run(id, {})).toEqual({ ok: false, message: NOT_OPEN_SENTENCE });
    await expect(door.read({ action: "object:visit.create" })).rejects.toThrow();
    expect(g.spy).not.toHaveBeenCalled();
  });

  for (const access of ["owner", "viewer"] as const) {
    it(`a mount, a focus and a refresh send no write (${access})`, async () => {
      const g = gateway(access === "viewer" ? { pkg: { access: "viewer", version: 1 } } : {});
      const html = await mount([listEvent({}), listEvent({ sort: { field: "title", dir: "asc" } }, "call-2")], g.impl);
      expect(cards(html)).toBe(2);
      // The package read is a GET, and every POST is the declared list.
      expect(g.calls.length).toBeGreaterThan(2);
      expect(writes(g.calls)).toEqual([]);
      for (const id of WRITE_IDS) expect(g.calls.some((c) => c.url.endsWith(`/actions/${id}`))).toBe(false);
    });
  }

  it("a viewer sees only the rows of the installed version", async () => {
    // J2a: the package answer of a viewer holds the manifest of the version
    // that the viewer installed. The card reads through its action ids only.
    const g = gateway({
      pkg: { access: "viewer", version: 1 },
      actions: [{ id: "find_v1", uses: "object:visit.list" }, { id: "peek_v1", uses: "object:visit.get" }],
      rows: (id) => (id === "find_v1" ? records("Installed", 2) : records("Draft", 2)),
    });
    const html = await mount([listEvent({})], g.impl);
    expect(html).toContain(">Installed-01<");
    expect(html).not.toContain("Draft-");
    const posts = g.calls.filter((c) => c.method === "POST");
    expect(posts.length).toBeGreaterThan(0);
    for (const c of posts) expect(c.url).toBe(`/api/studio/apps/${PKG}/actions/find_v1`);
  });

  it("the table does not edit", async () => {
    const node = recordsNode(OBJECT, { action: "object:visit.list", args: { limit: 25 } }, "Visits");
    expect((node.props.data as Record<string, unknown>).editable).toBeUndefined();
    const g = gateway();
    const html = await mount([listEvent({})], g.impl);
    expect(html).toContain(">Site-01<");
    expect(html).not.toContain("data-cell-edit");
    expect(html).not.toContain("data-edit-mark");
  });
});

describe("a card that has nothing to show draws nothing", () => {
  it("a 404 or Studio off draws no card and no error", async () => {
    const g = gateway({ status: 404 });
    const html = await mount([listEvent({})], g.impl);
    expect(html).toBe("");
    expect(g.calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  it("an object that the package does not hold, or one with no list, draws nothing", async () => {
    const gone = await mount([listEvent({ object: "invoice" })], gateway().impl);
    expect(gone).toBe("");
    clearAll();
    const g = gateway({ actions: ACTIONS.filter((a) => a.id !== "find") });
    expect(await mount([listEvent({})], g.impl)).toBe("");
    expect(g.calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  it("before the package read lands, it keeps a space and draws no error", () => {
    const html = renderToStaticMarkup(createElement(StudioToolCards, { toolEvents: [listEvent({})] }));
    expect(html).toContain('aria-hidden="true"');
    expect(html).not.toContain('role="alert"');
  });
});

describe("a failed read shows the sentence of the door once", () => {
  it("the frame draws the sentence once, with a retry only where one can help", () => {
    const sentence = "This app is busy now. Try again in 3 seconds.";
    const html = renderToStaticMarkup(createElement(RecordsFrame, {
      id: "studio-records:x", title: "Field visits · Visit", view: "error", error: sentence, empty: "", onRetry: () => {},
    }));
    expect(html.split(sentence).length - 1).toBe(1);
    expect(html.match(/role="alert"/g)?.length).toBe(1);
    expect(html).toContain("Try again");
    expect(html).toContain("Field visits · Visit");
    const refused = renderToStaticMarkup(createElement(RecordsFrame, {
      id: "studio-records:y", title: "Visit", view: "error", error: NOT_DECLARED_SENTENCE, empty: "", onRetry: () => {},
    }));
    expect(refused).toContain(NOT_DECLARED_SENTENCE);
    expect(refused).not.toContain("Try again");
  });

  it("an empty read says so in the frame", () => {
    const html = renderToStaticMarkup(createElement(RecordsFrame, {
      id: "studio-records:z", title: "Visit", view: "empty", error: null, empty: emptyLine({ filtered: true }), onRetry: () => {},
    }));
    expect(html).toContain("No records match this filter.");
  });
});

describe("a record write of Mira makes the card read its rows again (fix round 1, P1)", () => {
  it("a record write of Mira makes the card read its rows again", async () => {
    let stored = records("Site", 1);
    const g = gateway({ rows: () => stored });
    const events = [listEvent({})];
    const before = await mount(events, g.impl);
    expect(before).toContain(">Site-01<");
    expect(before).toContain("Planned");
    expect(before).not.toContain("New visit");

    // The key that the hook of the card watches (`useCachedResource` subscribes to it).
    const call = recordsCall(events[0])!;
    const key = sourceKey(cardDoor({ id: PKG, actions: ACTIONS }), call.source);
    let woke = 0;
    const off = subscribe(key, () => { woke += 1; });
    // A read of the records announces nothing, so it never wakes the card.
    announceAssistantWrite("studio_record_list");
    expect(woke).toBe(0);

    // Mira adds a record in the same chat, and the resend of the write ends.
    stored = [...stored, { id: "00000000-0000-4000-8000-000000000099", data: { title: "New visit", status: "Closed" } }];
    expect(announceAssistantWrite("studio_record_create")).toEqual(["studio"]);
    expect(woke).toBe(1);
    expect(peek(key)).toBeUndefined();
    off();

    // The hook reads again on that wake, through the same door.
    const door = cardDoor({ id: PKG, actions: ACTIONS });
    await cacheRead(key, () => door.read(call.source));
    const after = renderToStaticMarkup(createElement(StudioToolCards, { toolEvents: events }));
    expect(after).toContain(">New visit<");
    expect(after).toContain("Closed");
    expect(writes(g.calls)).toEqual([]);
  });
});

describe("a refusal of the package read (fix round 1, P2)", () => {
  it("a 403 draws no card and no error", async () => {
    // A guest of a room, or a member with a deny on feature:studio, sees the
    // call of another member's run. The package read answers 403.
    const g = gateway({ status: 403 });
    expect(await readCardPackage(PKG, g.impl)).toBeNull();
    clearAll();
    const html = await mount([listEvent({})], g.impl);
    expect(html).toBe("");
    expect(html).not.toContain('role="alert"');
    expect(g.calls.filter((c) => c.method === "POST")).toEqual([]);
  });

  it("a 401 says to sign in, with no retry", async () => {
    await expect(readCardPackage(PKG, gateway({ status: 401 }).impl)).rejects.toThrow(SIGN_IN_SENTENCE);
    expect(packageFailure(SIGN_IN_SENTENCE)).toEqual({ sentence: SIGN_IN_SENTENCE, retry: false });
    const html = renderToStaticMarkup(createElement(PackageFailedFrame, {
      id: "studio-records:s", title: "visit", error: SIGN_IN_SENTENCE, onRetry: () => {},
    }));
    expect(html).toContain(SIGN_IN_SENTENCE);
    expect(html).not.toContain("Try again");
    expect(html).not.toContain(LOAD_FAILED);
  });

  it("a fault of the server says to check the connection, with a retry", async () => {
    const err = await readCardPackage(PKG, gateway({ status: 500 }).impl).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    const failed = packageFailure((err as Error).message);
    expect(failed).toEqual({ sentence: LOAD_FAILED, retry: true });
    const html = renderToStaticMarkup(createElement(PackageFailedFrame, {
      id: "studio-records:f", title: "visit", error: (err as Error).message, onRetry: () => {},
    }));
    expect(html).toContain(LOAD_FAILED);
    expect(html).toContain("Try again");
  });
});

describe("the wiring", () => {
  const card = code(read(`${SRC}/components/studio/StudioRecordsCard.tsx`));
  const lib = code(read(`${SRC}/lib/studio/recordsCard.ts`));
  const tool = code(read(`${SRC}/components/studio/StudioToolCards.tsx`));

  it("the card builds its door through cardDoor only, and cardDoor holds the reads only", () => {
    expect(card).toContain("createDoorKeeper((o) => cardDoor({ id: o.packageId, actions: o.actions }))");
    expect(card).not.toMatch(/createRealDoor|previewControls/);
    expect(lib).toMatch(/createRealDoor\(\{ packageId: pkg\.id, actions: readActions\(pkg\.actions\)/);
  });

  it("the hooks read through the functions that `mount` runs", () => {
    expect(card).toContain("useCachedResource(packageKey(call.packageId), () => readCardPackage(call.packageId))");
    expect(card).toContain("door ? sourceKey(door, call.source) : null,");
    expect(card).toContain("() => (door as StudioDataDoor).read(call.source),");
    expect(card).toContain("<StudioDataContext.Provider value={door}>");
  });

  it("the card draws no Board, no Form, no button that acts, no edit and no delete", () => {
    expect(card).not.toMatch(/StudioForm|StudioBoard|StudioTable|DeleteRecordButton|TypedActionButton|editable/);
    expect(card).not.toMatch(/\.run\(|openAssistant|onAction=/);
    expect(lib).not.toMatch(/editable/);
  });

  it("a failed read draws the frame, not the table, so its sentence shows once", () => {
    const status = card.slice(card.indexOf('if (view !== "rows")'), card.indexOf("const more = moreLine"));
    expect(status).toContain("<RecordsFrame");
    expect(status).toContain("error={rows.error}");
    expect(status).not.toContain("GenerativeUINode");
  });

  it("every hook runs before the first return", () => {
    const body = card.slice(card.indexOf("export function StudioRecordsCard"));
    const firstReturn = body.indexOf("return");
    const hooks = [...body.matchAll(/\buse[A-Z]\w*(?:<[^>]*>)?\(/g)].map((m) => m.index ?? 0);
    expect(hooks.length).toBe(3);
    expect(Math.max(...hooks)).toBeLessThan(firstReturn);
  });

  it("StudioToolCards draws one records card for each read, after the approval cards", () => {
    expect(tool).toContain("const reads = recordsCalls(toolEvents);");
    expect(tool).toContain("<StudioRecordsCard key={call.key} call={call} />");
    expect(tool.indexOf("<StudioRequestCard")).toBeLessThan(tool.indexOf("<StudioRecordsCard"));
  });

  it("the one MessageBubble mount draws it", async () => {
    const bubble = code(read(`${SRC}/components/MessageBubble.tsx`));
    expect(bubble.match(/<StudioToolCards\b/g)?.length).toBe(1);
    const g = gateway();
    vi.stubGlobal("fetch", g.impl);
    await mount([listEvent({})], g.impl);
    const event = listEvent({});
    const message = {
      id: "a1", role: "assistant" as const, content: "Here are the visits.", timestamp: 1, streaming: false,
      toolEvents: [{ ...event, args: event.args ?? {} }],
    };
    const html = renderToStaticMarkup(createElement(MessageBubble, { message, sessionId: "s1" }));
    expect(cards(html)).toBe(1);
    expect(html).toContain(">Site-01<");
  });
});
