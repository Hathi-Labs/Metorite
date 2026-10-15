// WS-17 EM-G11c1b (email_app_master_plan.md §12.3.13.6b) — the chat pane of
// the Email app shows each confirmation card.
//
// R7 fence `email-pane-shows-cards`. The pane draws cards because it renders
// the shared `AgentChat`, which runs the email assistant on
// `/agent/run/stream` (`shows_cards=True`) and draws each
// `confirmation_requested` event in `ConfirmationQueue`. The route
// `/email/ai/chat` runs with `shows_cards=False` and forwards no card event,
// so a pane that called it would refuse every card. These two scans keep the
// pane on the one card path. The runner has no DOM, so both read the source.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..", "..", "..");

/** Each source file under `src/` that is not a test. */
function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      out.push(...sources(path));
    } else if (/\.(ts|tsx)$/.test(name) && !/\.(test|spec)\.(ts|tsx)$/.test(name)) {
      out.push(path);
    }
  }
  return out;
}

/** The literal path of each `fetch`-like call in `text`: `fetch`,
 *  `gatewayFetch` and every other name that ends in "fetch". */
function fetchPaths(text: string): string[] {
  const re = /\b\w*[Ff]etch\s*\(\s*(['"`])((?:(?!\1)[^\\]|\\.)*)\1/g;
  return Array.from(text.matchAll(re), (m) => m[2]);
}

/** True when a path ends in `ai/chat`, before any query. */
const namesAiChat = (path: string) => /\bai\/chat(?:[?#]|$)/.test(path);

describe("the pane renders the shared AgentChat", () => {
  it("renders <AgentChat with the chat session as its key", () => {
    const src = readFileSync(join(__dirname, "EmailAssistantChat.tsx"), "utf-8");
    expect(src).toMatch(/^import AgentChat from "@\/components\/AgentChat";\r?$/m);
    expect(src).toMatch(/<AgentChat\s+key=\{activeSession\.id\}/);
  });
});

describe("no client calls the email ai chat route", () => {
  it("finds the path in a call, so the scan can fail", () => {
    expect(fetchPaths('await gatewayFetch("/email/ai/chat", init)').filter(namesAiChat)).toEqual([
      "/email/ai/chat",
    ]);
    expect(fetchPaths("fetch(`${BASE}/api/email/ai/chat?x=1`)").filter(namesAiChat)).toHaveLength(1);
    expect(fetchPaths('fetch("/api/agent/chat")').filter(namesAiChat)).toEqual([]);
    expect(fetchPaths('fetch("/v1/openai/chat")').filter(namesAiChat)).toEqual([]);
  });

  it("no call of fetch or gatewayFetch names a path that ends in ai/chat", () => {
    const files = sources(SRC);
    let calls = 0;
    const hits: string[] = [];
    for (const file of files) {
      const paths = fetchPaths(readFileSync(file, "utf-8"));
      calls += paths.length;
      for (const p of paths.filter(namesAiChat)) hits.push(`${file}: ${p}`);
    }
    // A scan that read nothing proves nothing.
    expect(files.length).toBeGreaterThan(200);
    expect(calls).toBeGreaterThan(100);
    expect(hits).toEqual([]);
  });
});
