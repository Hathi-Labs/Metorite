/**
 * The card of the skill offer (STU-F6a, `ai_studio.md` §14 F6a).
 *
 * `vitest.config.ts` runs in `node` with no DOM, so this file draws the card
 * with `renderToStaticMarkup`, over a catalog that the one read cache
 * already holds. A press runs the press of `lib/studio/skillOffer.ts` with
 * the real `createSkill` over a stubbed `fetch`, so each test counts the
 * requests that leave the browser. The rules of the offer are in
 * `lib/studio/skillOffer.test.ts`.
 *
 * Mutations, each one red here (2026-11-14):
 * - return true from `skillOfferOn`: "the flag off draws nothing and reads nothing";
 * - drop the `!on` return of the card: "the flag off draws nothing" (card alone);
 * - send `{ ...offer.draft, publish: true }` or a second create from
 *   `pressSave`: "Save sends one POST with the body that the card shows";
 * - draw nothing for a refusal: "a 422 shows its text";
 * - draw a card when the catalog read gives null: "a catalog 404 draws nothing";
 * - mount `SkillOfferCard` a second time, in `MessageBubble` or in another
 *   file: "one mount, in StudioToolCards";
 * - drop the typed mark from the user message in `useAgentChat`: "the live
 *   user message keeps the typed mark".
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ToolEvent } from "@/components/MarkdownMessage";
import { clearAll, put } from "@/lib/dataCache";
import { classWord } from "@/lib/studio/skillForm";
import {
  OFFER_TITLE,
  SAVED_LINE,
  __forgetOffersForTests,
  offerLocal,
  pressDismiss,
  pressSave,
  skillOffer,
  type OfferTurn,
} from "@/lib/studio/skillOffer";
import { skillOfferOn } from "@/lib/studio/skillOfferFlag";
import { createSkill, type CatalogAction } from "@/lib/studio/skillsApi";

import SkillOfferCard, { OFFER_CATALOG_KEY, loadOfferCatalog } from "./SkillOfferCard";
import StudioToolCards from "./StudioToolCards";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");
/** Code and markup only: no comments. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

const WORDS = "Find the onboarding page and propose a short summary page";
const CATALOG: CatalogAction[] = [
  { id: "knowledge.list_spaces", title: "List the spaces that the member can open.", source: "manifest", class: "A", tool: "list_spaces" },
  { id: "knowledge.get_page", title: "Read one page, with its body, versions and links.", source: "manifest", class: "A", tool: "read_page" },
  { id: "knowledge.propose", title: "Propose a new page or an edit of a page.", source: "manifest", class: "B", tool: "propose_page" },
];

function event(name: string, n: number): ToolEvent {
  return { id: `call-${n}`, name, args: { title: "an argument" }, result: "a result", status: "done" };
}
const EVENTS: ToolEvent[] = [event("list_spaces", 1), event("read_page", 2), event("propose_page", 3)];

function turn(extra: Partial<OfferTurn> = {}): OfferTurn {
  return {
    messageId: "m-1",
    streaming: false,
    solo: true,
    ranByViewer: true,
    prompt: { words: WORDS, typed: true, byViewer: true },
    calls: EVENTS,
    ...extra,
  };
}

const draw = (t: OfferTurn = turn()) =>
  renderToStaticMarkup(createElement(StudioToolCards, { toolEvents: EVENTS, sessionId: "chat-1", offer: t }));

/** Each request that leaves the browser. */
interface Sent { url: string; method: string; body: unknown }
function stubFetch(answer: (url: string) => { status: number; body: unknown }): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    sent.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const { status, body } = answer(url);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
  return sent;
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_SKILL_OFFER", "1");
  put(OFFER_CATALOG_KEY, CATALOG);
});

afterEach(() => {
  clearAll();
  __forgetOffersForTests();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("the flag (NEXT_PUBLIC_SKILL_OFFER)", () => {
  it("is off by default and on only for 1", () => {
    vi.stubEnv("NEXT_PUBLIC_SKILL_OFFER", "");
    expect(skillOfferOn()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_SKILL_OFFER", "true");
    expect(skillOfferOn()).toBe(false);
    vi.stubEnv("NEXT_PUBLIC_SKILL_OFFER", "1");
    expect(skillOfferOn()).toBe(true);
  });

  it("the flag off draws nothing and reads nothing", () => {
    vi.stubEnv("NEXT_PUBLIC_SKILL_OFFER", "");
    const sent = stubFetch(() => ({ status: 200, body: { actions: CATALOG } }));
    expect(draw()).toBe("");
    expect(renderToStaticMarkup(createElement(SkillOfferCard, { turn: turn() }))).toBe("");
    expect(sent).toEqual([]);
  });

  it("the flag on draws the card", () => {
    expect(draw()).toContain(OFFER_TITLE);
  });
});

describe("the card shows the skill that Save makes", () => {
  it("shows the name, the description, the instructions and each action with its class", () => {
    const offer = skillOffer(turn(), CATALOG, false)!;
    const html = draw();
    expect(html.match(/data-skill-offer="/g)?.length).toBe(1);
    expect(html).toContain(`>${offer.draft.name}<`);
    expect(html).toContain(`>${offer.draft.description}<`);
    // React escapes nothing in the instructions but the Markdown marks stay text.
    expect(html).toContain(WORDS);
    expect(html).toContain("## Steps");
    for (const row of CATALOG) {
      expect(html).toContain(`data-skill-offer-action="${row.id}"`);
      expect(html).toContain(`>${row.title}<`);
    }
    expect(html).toContain(`>${classWord("A")}<`);
    expect(html).toContain(`>${classWord("B")}<`);
    expect(html).toContain("Save");
    expect(html).toContain("Not now");
    expect(html).not.toContain("an argument");
    expect(html).not.toContain("a result");
  });

  it("a turn that the rule refuses draws nothing", () => {
    expect(draw(turn({ streaming: true }))).toBe("");
    expect(draw(turn({ solo: false }))).toBe("");
    expect(draw(turn({ calls: EVENTS.slice(0, 2) }))).toBe("");
  });

  it("a catalog 404 draws nothing", async () => {
    put(OFFER_CATALOG_KEY, null);
    expect(draw()).toBe("");
    stubFetch(() => ({ status: 404, body: { detail: "Not found" } }));
    expect(await loadOfferCatalog()).toBeNull();
    stubFetch(() => ({ status: 403, body: { detail: "Forbidden" } }));
    expect(await loadOfferCatalog()).toBeNull();
    stubFetch(() => ({ status: 200, body: { actions: CATALOG } }));
    expect(await loadOfferCatalog()).toEqual(CATALOG);
  });
});

describe("Save and Not now", () => {
  it("Save sends one POST with the body that the card shows, and never a publish", async () => {
    const offer = skillOffer(turn(), CATALOG, false)!;
    const html = draw();
    const sent = stubFetch(() => ({ status: 201, body: { skill: { id: "sk-9" }, version: null } }));
    await pressSave(offer, createSkill);
    expect(sent).toHaveLength(1);
    expect(sent[0].url).toBe("/api/studio/skills");
    expect(sent[0].method).toBe("POST");
    expect(sent[0].body).toEqual({
      name: offer.draft.name,
      description: offer.draft.description,
      body: {
        description: offer.draft.description,
        instructions: offer.draft.body.instructions,
        actions: ["knowledge.list_spaces", "knowledge.get_page", "knowledge.propose"],
      },
    });
    // Each text of the body is the text that the card showed.
    expect(html).toContain(`>${offer.draft.name}<`);
    expect(html).toContain(`>${offer.draft.description}<`);
    expect(sent.some((s) => /state|publish/.test(s.url))).toBe(false);
    // The card then links to the draft in Studio.
    const after = draw();
    expect(after).toContain(SAVED_LINE);
    expect(after).toContain('href="/studio/skills/sk-9"');
    expect(after).not.toContain('data-skill-offer-save=""');
  });

  it("a double click sends no second POST", async () => {
    const offer = skillOffer(turn(), CATALOG, false)!;
    const sent = stubFetch(() => ({ status: 201, body: { skill: { id: "sk-1" }, version: null } }));
    await Promise.all([pressSave(offer, createSkill), pressSave(offer, createSkill)]);
    expect(sent).toHaveLength(1);
  });

  it("a 422 shows its text", async () => {
    const offer = skillOffer(turn(), CATALOG, false)!;
    stubFetch(() => ({ status: 422, body: { detail: "$.instructions: a skill needs instructions" } }));
    await pressSave(offer, createSkill);
    const html = draw();
    expect(html).toContain('role="alert"');
    expect(html).toContain("$.instructions: a skill needs instructions");
    expect(html).toContain('data-skill-offer-save=""');
  });

  it("Not now sends nothing, and the card draws nothing after it", () => {
    const sent = stubFetch(() => ({ status: 201, body: {} }));
    pressDismiss("m-1");
    expect(offerLocal("m-1").kind).toBe("dismissed");
    expect(draw()).toBe("");
    expect(sent).toEqual([]);
  });
});

/** Every source file under `src/`, as a path relative to it. */
function sources(dir = SRC, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name !== "node_modules") sources(full, out);
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(full.slice(SRC.length).replace(/\\/g, "/").replace(/^\//, ""));
    }
  }
  return out;
}

describe("the wiring", () => {
  it("one mount, in StudioToolCards, which the one MessageBubble mount draws", () => {
    const mounts = sources().filter((f) => /<SkillOfferCard\b/.test(code(read(f))));
    expect(mounts).toEqual(["components/studio/StudioToolCards.tsx"]);
    const bubble = code(read("components/MessageBubble.tsx"));
    expect(bubble.match(/<StudioToolCards\b/g)?.length).toBe(1);
    expect(bubble).toContain("prompt: offerPrompt(skillOfferOf.prompt, viewerEmail),");
    expect(bubble).toContain("calls: dedupedToolEvents,");
    const tool = code(read("components/studio/StudioToolCards.tsx"));
    expect(tool).toContain("const offered = offer && skillOfferOn() ? offer : null;");
    expect(tool.indexOf("<StudioRecordsCard")).toBeLessThan(tool.indexOf("<SkillOfferCard"));
  });

  it("AgentChat hands each assistant turn its prompt and whether the chat is solo", () => {
    const chat = code(read("components/AgentChat.tsx"));
    expect(chat).toContain("{ prompt: promptBefore(visibleMessages, i), solo: !isRoom }");
  });

  it("the live user message keeps the typed mark, and never for a card answer", () => {
    const hook = code(read("hooks/useAgentChat.ts"));
    expect(hook.match(/\.\.\.\(opts\?\.typed === true && !opts\?\.cardAnswer \? \{ typed: true \} : \{\}\)/g)?.length).toBe(2);
  });

  it("the card reads the catalog only through the read cache, and only for a turn that asks", () => {
    const card = code(read("components/studio/SkillOfferCard.tsx"));
    expect(card).toContain("useCachedResource(asks ? OFFER_CATALOG_KEY : null, loadOfferCatalog)");
    expect(card).not.toMatch(/\bfetch\(/);
    // No palette, bell, rail or command bar in a chat card (R9).
    expect(card).not.toMatch(/NotificationBell|AgentChat|CommandPalette|addEventListener\("keydown"/);
  });

  it("every hook runs before the first return", () => {
    const card = code(read("components/studio/SkillOfferCard.tsx"));
    const body = card.slice(card.indexOf("export default function SkillOfferCard"));
    const firstReturn = body.indexOf("return");
    const hooks = [...body.matchAll(/\buse[A-Z]\w*(?:<[^>]*>)?\(/g)].map((m) => m.index ?? 0);
    expect(hooks.length).toBe(2);
    expect(Math.max(...hooks)).toBeLessThan(firstReturn);
  });
});
