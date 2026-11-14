/**
 * The Connect card of a saved draft, drawn in the chat (STU-I8d, `ai_studio.md` §14 row I8d).
 *
 * `vitest.config.ts` runs in `node` with no DOM, so this file draws the card with `renderToStaticMarkup`
 * over rows that the one read cache already holds, as the records card test does.
 *
 * Mutations, each one red here (2026-11-14):
 * - drop the `status !== "done"` guard of `connectCall`: "a running or failed call draws no card";
 * - drop the `ok !== true` check of `savedPackageId`: "a refusal draws no card";
 * - drop the dedupe of `connectCalls`: "two saves of one package draw one card";
 * - add an `<Input>` to the card: "the card source holds no Input, no textarea and no key field";
 * - drop the connect mount in `StudioToolCards`: "StudioToolCards mounts the connect card".
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import type { ToolEvent } from "@/components/MarkdownMessage";
import { announceAssistantWrite, CONNECTIONS_KEY_PREFIX } from "@/lib/assistantWrites";
import { placementOf } from "@/lib/chatPlacement";
import { clearAll, peek, put } from "@/lib/dataCache";
import { connectionsKey, type ConnectionRow } from "@/lib/studio/connect";
import { CONNECT_CARD_TOOLS, connectCall, connectCalls, savedPackageId } from "@/lib/studio/connectCard";

import StudioToolCards from "./StudioToolCards";

const PKG = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const OTHER = "11111111-2222-4333-8444-555555555555";
const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");
/** Code and markup only: no comments. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

const ok = (pid: string) => JSON.stringify({ ok: true, errors: [], package_id: pid, note: "saved" });
function ev(name: string, result: string, status: ToolEvent["status"] = "done", id = `e-${name}`): ToolEvent {
  return { id, name, args: {}, result, status, startedAt: 1, endedAt: 2 };
}
const row = (over: Partial<ConnectionRow>): ConnectionRow => ({
  name: "mcp:hubspot", label: "hubspot", met: false, checkable: true,
  setupHref: "/integrations?add=mcp&name=hubspot", canConnect: true, ...over,
});
const draw = (events: ToolEvent[]) => renderToStaticMarkup(createElement(StudioToolCards, { toolEvents: events }));

afterEach(() => clearAll());

describe("which event draws a card", () => {
  it("a finished, successful studio_save or template create names its package", () => {
    expect(connectCall(ev("studio_save", ok(PKG)))?.packageId).toBe(PKG);
    expect(connectCall(ev("studio_create_from_template", ok(PKG)))?.packageId).toBe(PKG);
    expect(connectCall(ev("mcp__studio__studio_save", ok(PKG.toUpperCase())))?.packageId).toBe(PKG);
  });

  it("a running or failed call draws no card", () => {
    expect(connectCall(ev("studio_save", ok(PKG), "running"))).toBeNull();
    expect(connectCall(ev("studio_save", ok(PKG), "error"))).toBeNull();
    expect(connectCall(ev("studio_save", ""))).toBeNull();
  });

  it("a refusal, or a held save with no id, draws no card", () => {
    expect(connectCall(ev("studio_save", JSON.stringify({ ok: false, package_id: PKG })))).toBeNull();
    expect(connectCall(ev("studio_save", JSON.stringify({ ok: true, decision: "revised" })))).toBeNull();
    expect(connectCall(ev("studio_save", `{'ok': False, 'package_id': '${PKG}'}`))).toBeNull();
  });

  it("a cut result in a Python repr still names the package", () => {
    expect(savedPackageId(`{'ok': True, 'package_id': '${PKG}', 'outline': 'cut`)).toBe(PKG);
  });

  it("a package id that is not a UUID draws no card", () => {
    expect(connectCall(ev("studio_save", ok("not-a-uuid")))).toBeNull();
    expect(connectCall(ev("studio_save", ok(`${PKG}/../x`)))).toBeNull();
  });

  it("another tool draws no card", () => {
    expect(connectCall(ev("studio_publish", ok(PKG)))).toBeNull();
    expect(connectCall(ev("studio_record_list", ok(PKG)))).toBeNull();
  });

  it("two saves of one package draw one card, and two packages draw two", () => {
    const events = [
      ev("studio_save", ok(PKG), "done", "a"), ev("studio_save", ok(PKG), "done", "b"),
      ev("studio_create_from_template", ok(OTHER), "done", "c"),
    ];
    expect(connectCalls(events).map((c) => [c.eventId, c.packageId])).toEqual([["a", PKG], ["c", OTHER]]);
    expect(connectCalls(undefined)).toEqual([]);
  });
});

describe("the card", () => {
  it("StudioToolCards draws one card with a Connect for each unmet name, and no field", () => {
    put(connectionsKey(PKG), [
      row({}), row({ name: "mcp:slack", label: "slack" }), row({ name: "gmail", label: "Gmail", met: true }),
    ]);
    const html = draw([ev("studio_save", ok(PKG), "done", "a"), ev("studio_save", ok(PKG), "done", "b")]);
    expect(html.match(/data-studio-connect=/g)).toHaveLength(1);
    expect(html).toContain("2 connections need attention");
    expect(html).toContain("This app needs the MCP server hubspot.");
    expect(html).toContain('aria-label="Connect the MCP server slack"');
    expect(html).toContain("Gmail is connected.");
    expect(html).not.toMatch(/<input|<textarea/i);
  });

  it("draws nothing when each name is met, when no row exists (a 404), or before the read lands", () => {
    put(connectionsKey(PKG), [row({ met: true })]);
    expect(draw([ev("studio_save", ok(PKG))])).toBe("");
    put(connectionsKey(PKG), []);
    expect(draw([ev("studio_save", ok(PKG))])).toBe("");
    clearAll();
    expect(draw([ev("studio_save", ok(PKG))])).toBe("");
  });

  it("a save that still runs draws nothing", () => {
    put(connectionsKey(PKG), [row({})]);
    expect(draw([ev("studio_save", ok(PKG), "running")])).toBe("");
  });

  it("a member who may not connect reads the ask line and has no Connect", () => {
    put(connectionsKey(PKG), [row({ canConnect: false })]);
    const html = draw([ev("studio_save", ok(PKG))]);
    expect(html).toContain("An admin must connect the MCP server hubspot.");
    expect(html).not.toContain("Connect the MCP server");
  });
});

describe("the wiring", () => {
  it("the card source holds no Input, no textarea and no key field", () => {
    const src = code(read("components/studio/StudioConnectCard.tsx"));
    expect(src).not.toMatch(/Input|textarea|password|apiKey|api_key/i);
  });

  it("the card reads the I8a route through getConnections and draws ConnectLines", () => {
    const src = code(read("components/studio/StudioConnectCard.tsx"));
    expect(src).toContain("getConnections(call.packageId)");
    expect(src).toContain("ConnectLines");
    expect(src).toContain("connectPanel(conns.data, conns.error, call.packageId)");
  });

  it("the return stays the I8a one: the prefix of the guard is still /studio/", () => {
    expect(code(read("lib/studio/returnPath.ts"))).toContain('STUDIO_PREFIX = "/studio/"');
  });

  it("StudioToolCards mounts the connect card", () => {
    const src = code(read("components/studio/StudioToolCards.tsx"));
    expect(src).toContain("connectCalls(toolEvents)");
    expect(src).toContain("<StudioConnectCard");
  });

  it("a save drops the connections reads, and no tool of the card is a waiting card", () => {
    expect(connectionsKey("x").startsWith(CONNECTIONS_KEY_PREFIX)).toBe(true);
    put(connectionsKey(PKG), [row({})]);
    announceAssistantWrite("studio_save");
    expect(peek(connectionsKey(PKG))).toBeUndefined();
    put(connectionsKey(PKG), [row({})]);
    announceAssistantWrite("studio_record_list");
    expect(peek(connectionsKey(PKG))).toBeDefined();
    for (const n of CONNECT_CARD_TOOLS) expect(placementOf(n), n).not.toBe("ask");
  });
});
