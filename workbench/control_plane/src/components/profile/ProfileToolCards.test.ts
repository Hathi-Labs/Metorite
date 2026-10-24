/**
 * The receipt of an assistant profile write, and its Undo (WS-63 PZ-6a).
 *
 * The server contract: each result of `acb_skills/profile_tools.py` ends with
 * ONE machine line, `undo: {json}`. The card parses it, never shows it, and
 * makes the inverse call through `/api/auth/me/assistant-profile/...`.
 *
 * Mutations this file catches (R7):
 * - the card shows the machine line;
 * - an Undo calls the wrong route, the wrong verb, or not the prior value;
 * - a result with no undo line, or a line that is not the last, gets Undo;
 * - a path outside `facts` and `aliases` reaches a URL.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ToolEvent } from "@/components/MarkdownMessage";
import { placementOf } from "@/lib/chatPlacement";
import ProfileToolCards, {
  PROFILE_CARD_TOOLS,
  parseUndo,
  performUndo,
  readProfileResult,
  undoRequest,
  type ProfileUndo,
} from "./ProfileToolCards";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push() {} }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));

const REMEMBER =
  "Saved: «I like green tea». To undo: profile_forget(f0101).\n" +
  'undo: {"op":"delete","path":"facts/f0101","v":1}';
const SET =
  'Set style: {"language": "en", "length": "detailed"}. To undo: profile_set(style, {"language": "en", "length": "short"}).\n' +
  'undo: {"key":"style","op":"put","v":1,"value":{"language":"en","length":"short"}}';
const FORGET =
  'Forgot: «I work from Pune». To undo: profile_remember("I work from Pune", "work").\n' +
  'undo: {"body":{"kind":"work","text":"I work from Pune"},"op":"post","path":"facts","v":1}';
const ALIAS =
  'Changed alias «Raj» to <raj.k@alpha.example>. To undo: profile_alias("Raj", "raj@alpha.example").\n' +
  'undo: {"body":{"email":"raj@alpha.example"},"op":"patch","path":"aliases/a0001","v":1}';

function ev(name: string, result: string): ToolEvent {
  return { id: `e-${name}`, name, args: {}, result, status: "done", startedAt: 1, endedAt: 2 };
}

function fakeFetch() {
  const calls: { url: string; init: RequestInit }[] = [];
  const fn = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(null, { status: 204 });
  }) as unknown as typeof fetch;
  return { calls, fn };
}

describe("the machine line", () => {
  it("parses each of the four inverse calls", () => {
    expect(readProfileResult(REMEMBER).undo).toEqual({ v: 1, op: "delete", path: "facts/f0101" });
    expect(readProfileResult(SET).undo).toEqual({
      v: 1, op: "put", key: "style", value: { language: "en", length: "short" },
    });
    expect(readProfileResult(FORGET).undo).toEqual({
      v: 1, op: "post", path: "facts", body: { kind: "work", text: "I work from Pune" },
    });
    expect(readProfileResult(ALIAS).undo).toEqual({
      v: 1, op: "patch", path: "aliases/a0001", body: { email: "raj@alpha.example" },
    });
  });

  it("never shows the line", () => {
    for (const r of [REMEMBER, SET, FORGET, ALIAS]) {
      expect(readProfileResult(r).text).not.toMatch(/undo|profile_/i);
      expect(readProfileResult(r).text.split("\n")).toHaveLength(1);
    }
    const html = renderToStaticMarkup(
      createElement(ProfileToolCards, { toolEvents: [ev("profile_remember", REMEMBER)] }),
    );
    expect(html).toContain("Saved: «I like green tea»");
    expect(html).not.toContain("undo:");
    expect(html).not.toContain("facts/f0101");
    expect(html).not.toContain("profile_forget");
    expect(html).toContain("Undo");
  });

  it("a result with no undo line shows no Undo", () => {
    for (const r of ["Already saved.", "Learning is off. Nothing saved.",
      "Saved.\nundo: {not json}", 'undo: {"op":"delete","path":"facts/f1","v":1}\nlater line']) {
      expect(readProfileResult(r).undo).toBeNull();
    }
    const html = renderToStaticMarkup(
      createElement(ProfileToolCards, { toolEvents: [ev("profile_remember", "Already saved.")] }),
    );
    expect(html).toContain("Already saved.");
    expect(html).not.toContain("Undo");
  });

  it("refuses a path, a verb or a key the relays do not serve", () => {
    for (const line of [
      'undo: {"op":"delete","path":"../","v":1}',
      'undo: {"op":"delete","path":"facts","v":1}',
      'undo: {"op":"delete","path":"facts/..","v":1}',
      'undo: {"op":"delete","path":"settings/x","v":1}',
      'undo: {"op":"post","path":"facts/f1","body":{"text":"x"},"v":1}',
      'undo: {"op":"patch","path":"aliases","body":{"email":"x"},"v":1}',
      'undo: {"op":"put","key":"learning","value":{},"v":1}',
      'undo: {"op":"get","path":"facts","v":1}',
      'undo: {"op":"delete","path":"facts/f1","v":2}',
    ]) {
      expect(parseUndo(line)).toBeNull();
    }
  });
});

describe("Undo calls the inverse route with the prior value", () => {
  const cases: [string, ProfileUndo, string, string, unknown][] = [
    ["delete", { v: 1, op: "delete", path: "facts/f0101" },
      "/api/auth/me/assistant-profile/facts/f0101", "DELETE", undefined],
    ["put", { v: 1, op: "put", key: "style", value: { length: "short" } },
      "/api/auth/me/assistant-profile", "PUT", { key: "style", value: { length: "short" } }],
    ["post", { v: 1, op: "post", path: "facts", body: { text: "I work from Pune", kind: "work" } },
      "/api/auth/me/assistant-profile/facts", "POST", { text: "I work from Pune", kind: "work" }],
    ["patch", { v: 1, op: "patch", path: "aliases/a0001", body: { email: "raj@alpha.example" } },
      "/api/auth/me/assistant-profile/aliases/a0001", "PATCH", { email: "raj@alpha.example" }],
  ];
  for (const [name, undo, url, method, body] of cases) {
    it(name, async () => {
      expect(undoRequest(undo).url).toBe(url);
      const f = fakeFetch();
      expect(await performUndo(undo, f.fn)).toBe(true);
      expect(f.calls).toHaveLength(1);
      expect(f.calls[0].url).toBe(url);
      expect(f.calls[0].init.method).toBe(method);
      const sent = f.calls[0].init.body;
      expect(sent === undefined ? undefined : JSON.parse(String(sent))).toEqual(body);
    });
  }

  it("a refused undo reports false", async () => {
    const fn = (async () => new Response("{}", { status: 422 })) as unknown as typeof fetch;
    expect(await performUndo({ v: 1, op: "delete", path: "facts/f1" }, fn)).toBe(false);
  });
});

describe("placement", () => {
  it("each profile write is a write receipt", () => {
    expect(PROFILE_CARD_TOOLS.map((n) => placementOf(n))).toEqual(["write", "write", "write", "write"]);
  });
});
