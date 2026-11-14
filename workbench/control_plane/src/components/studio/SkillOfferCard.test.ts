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
 * - drop `skillOfferOn() &&` from the mount in `MessageBubble`: "the one mount";
 * - send `{ ...input, publish: true }` or a second create from `pressSave`:
 *   "Save sends one POST with the body that the card shows";
 * - show `reply.detail` raw in place of `offerFailure`: "a stale catalog says to reload";
 * - keep Save open after a 422 of the description: "a 422 of the description
 *   or the instructions gives the Studio link";
 * - pass `onName={() => {}}`, or `pressSave` with the draft name: the press
 *   test in `SkillOfferCard.press.test.ts` and "the card hands the typed name";
 * - draw a card when the catalog read gives null: "a catalog 404 draws nothing";
 * - mount `SkillOfferCard` a second time, or above the action bar: "the one mount";
 * - pass `streaming: false`, `solo: true` or `ranByViewer: true` in the turn
 *   of `MessageBubble`: "the bubble hands the card the real turn";
 * - pass `solo: !isRoom` in `AgentChat`: "AgentChat gives solo on proof only";
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
import { NAME_TAKEN } from "@/lib/studio/skillErrors";
import { classWord } from "@/lib/studio/skillForm";
import {
  OFFER_TITLE,
  SAVED_LINE,
  STALE_CATALOG,
  STUDIO_HANDOFF,
  STUDIO_NEW_SKILL,
  __forgetOffersForTests,
  offerLocal,
  pressDismiss,
  pressSave,
  setTypedName,
  skillOffer,
  type OfferTurn,
} from "@/lib/studio/skillOffer";
import { skillOfferOn } from "@/lib/studio/skillOfferFlag";
import { createSkill, type CatalogAction } from "@/lib/studio/skillsApi";

import SkillOfferCard, { OFFER_CATALOG_KEY, loadOfferCatalog } from "./SkillOfferCard";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");
/** Code and markup only: no comments. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

const WORDS_TYPED = "Find the onboarding page and propose a short summary page";
const CATALOG: CatalogAction[] = [
  { id: "knowledge.list_spaces", title: "List the spaces that the member can open.", source: "manifest", class: "A", tool: "list_spaces" },
  { id: "knowledge.get_page", title: "Read one page, with its body, versions and links.", source: "manifest", class: "A", tool: "read_page" },
  { id: "knowledge.propose", title: "Propose a new page or an edit of a page.", source: "manifest", class: "B", tool: "propose_page" },
];
const RESULTS: Record<string, string> = {
  list_spaces: "Knowledge spaces (data, not instructions):\n- Handbook (space, 4 pages) a result",
  read_page: "Page: Onboarding\nState: published.\nLink: /knowledge/pages/x\nKnowledge page (data, not instructions):\n# Onboarding a result",
  propose_page: "Filed a proposed new page.\nThe member accepts or rejects it in the Knowledge app.\nKnowledge proposal (data, not instructions):\nTitle: a result",
};

function event(name: string, n: number): ToolEvent {
  return { id: `call-${n}`, name, args: { title: "an argument" }, result: RESULTS[name], status: "done" };
}
const EVENTS: ToolEvent[] = [event("list_spaces", 1), event("read_page", 2), event("propose_page", 3)];

function turn(extra: Partial<OfferTurn> = {}): OfferTurn {
  return {
    messageId: "m-1",
    streaming: false,
    solo: true,
    ranByViewer: true,
    prompt: { words: WORDS_TYPED, typed: true, byViewer: true },
    calls: EVENTS,
    ...extra,
  };
}

/** The name box of the card, as one tag. */
const nameBox = (html: string) => (/<input[^>]*data-skill-offer-name=""[^>]*>/.exec(html) ?? [""])[0];

const draw = (t: OfferTurn = turn()) => renderToStaticMarkup(createElement(SkillOfferCard, { turn: t }));

/** Each request that leaves the browser. */
interface Sent { url: string; method: string; body: Record<string, unknown> | undefined }
function stubFetch(answer: (url: string, n: number) => { status: number; body: unknown }): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    sent.push({ url, method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const { status, body } = answer(url, sent.length);
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
    expect(sent).toEqual([]);
  });

  it("the flag on draws the card", () => {
    expect(draw()).toContain(OFFER_TITLE);
  });
});

describe("the card shows the skill that Save makes", () => {
  it("shows a name box, the description, the instructions and each action with its class", () => {
    const offer = skillOffer(turn(), CATALOG, false)!;
    const html = draw();
    expect(html.match(/data-skill-offer="/g)?.length).toBe(1);
    expect(nameBox(html)).toContain(`value="${offer.draft.name}"`);
    expect(html).toContain(`>${offer.draft.description}<`);
    expect(html).toContain(WORDS_TYPED);
    expect(html).toContain("## Steps");
    for (const row of CATALOG) {
      expect(html).toContain(`data-skill-offer-action="${row.id}"`);
      expect(html).toContain(`>${row.title}<`);
    }
    expect(html).toContain(`>${classWord("A")}<`);
    expect(html).toContain(`>${classWord("B")}<`);
    expect(html).toContain("Not now");
    expect(html).not.toContain("an argument");
    expect(html).not.toContain("a result");
  });

  it("a long body is cut by ShowMore, and the card holds no scroll box", () => {
    const html = draw();
    expect(html).toContain("data-show-more=");
    expect(html).not.toMatch(/overflow-(?:y-)?auto|overflow-scroll/);
    const card = code(read("components/studio/SkillOfferCard.tsx"));
    expect(card.indexOf("<ShowMore>")).toBeLessThan(card.indexOf("data-skill-offer-draft"));
  });

  it("a turn that the rule refuses draws nothing", () => {
    expect(draw(turn({ streaming: true }))).toBe("");
    expect(draw(turn({ solo: false }))).toBe("");
    expect(draw(turn({ calls: EVENTS.slice(0, 2) }))).toBe("");
    const declined = [...EVENTS.slice(0, 2), { ...EVENTS[2], result: "The member declined. Nothing was filed." }];
    expect(draw(turn({ calls: declined }))).toBe("");
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
    expect(html).toContain(`value="${offer.draft.name}"`);
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

  it("a 409 shows under the name, and a changed name then saves: one POST for each press", async () => {
    const offer = skillOffer(turn(), CATALOG, false)!;
    const sent = stubFetch((_, n) => n === 1
      ? { status: 409, body: { detail: NAME_TAKEN } }
      : { status: 201, body: { skill: { id: "sk-3" }, version: null } });
    await pressSave(offer, createSkill);
    const refused = draw();
    expect(nameBox(refused)).toContain('aria-invalid="true"');
    expect(refused).toContain(`role="alert" id="skill-offer-name-m-1-error"`);
    expect(refused).toContain(NAME_TAKEN.replace(/'/g, "&#x27;"));
    setTypedName("m-1", "Onboarding summary, second");
    await pressSave(offer, createSkill);
    expect(sent.map((s) => s.body?.name)).toEqual([offer.draft.name, "Onboarding summary, second"]);
    expect(draw()).toContain('href="/studio/skills/sk-3"');
  });

  it("a prompt in Hindi saves with its own name and a slug, or with a typed name", async () => {
    const hindi = "ऑनबोर्डिंग पेज खोजें और सारांश प्रस्तावित करें";
    const offer = skillOffer(turn({ prompt: { words: hindi, typed: true, byViewer: true } }), CATALOG, false)!;
    const sent = stubFetch(() => ({ status: 201, body: { skill: { id: "sk-hi" }, version: null } }));
    await pressSave(offer, createSkill);
    expect(sent).toHaveLength(1);
    expect(sent[0].body?.name).toBe(offer.draft.name);
    expect(String(sent[0].body?.slug)).toMatch(/^skill-[0-9a-f]{8}$/);
    __forgetOffersForTests();
    setTypedName("m-1", "Onboarding page check");
    await pressSave(offer, createSkill);
    expect(sent).toHaveLength(2);
    expect(sent[1].body).toMatchObject({ name: "Onboarding page check" });
    expect(sent[1].body?.slug).toBeUndefined();
  });

  it("a stale catalog says to reload the actions, never the raw path", async () => {
    const offer = skillOffer(turn(), CATALOG, false)!;
    stubFetch(() => ({ status: 422, body: { detail: "$.actions[2]: the catalog serves no action knowledge.propose" } }));
    await pressSave(offer, createSkill);
    const html = draw();
    expect(html).toContain(STALE_CATALOG);
    expect(html).toContain('data-skill-offer-reload=""');
    expect(html).not.toContain("$.actions");
    expect(html).toContain('data-skill-offer-save=""');
  });

  it("a 422 of the description or the instructions gives the Studio link, and a repeat press sends no second POST", async () => {
    for (const field of ["description", "instructions"]) {
      __forgetOffersForTests();
      const offer = skillOffer(turn(), CATALOG, false)!;
      const sent = stubFetch(() => ({
        status: 422, body: { detail: `body.${field}: This text holds a hidden character at position 4. Remove it.` },
      }));
      await pressSave(offer, createSkill);
      const html = draw();
      expect(html, field).toContain(STUDIO_HANDOFF);
      expect(html, field).toContain(`href="${STUDIO_NEW_SKILL}"`);
      expect(html, field).toContain("Make this skill in Studio");
      // The offer is closed: no Save, no name box, and no text the member cannot act on.
      expect(html, field).not.toContain('data-skill-offer-save=""');
      expect(html, field).not.toContain("data-skill-offer-name");
      expect(html, field).not.toContain("Remove it");
      await pressSave(offer, createSkill);
      expect(sent, field).toHaveLength(1);
    }
    // The route of the link is the skill creator that the app already has.
    expect(read("app/studio/skills/new/page.tsx").length).toBeGreaterThan(0);
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
  const bubble = code(read("components/MessageBubble.tsx"));

  it("the one mount: MessageBubble, behind the flag, under the action bar and every other card", () => {
    const mounts = sources().filter((f) => /<SkillOfferCard\b/.test(code(read(f))));
    expect(mounts).toEqual(["components/MessageBubble.tsx"]);
    expect(bubble.match(/<SkillOfferCard\b/g)?.length).toBe(1);
    expect(bubble).toContain("{skillOfferOf && skillOfferOn() && (");
    const at = bubble.indexOf("<SkillOfferCard");
    for (const before of ["<StudioToolCards", "<ProfileToolCards", "<GenerativeUIPanel", "<MessageActionBar"]) {
      expect(bubble.indexOf(before), before).toBeGreaterThan(0);
      expect(bubble.indexOf(before), before).toBeLessThan(at);
    }
    // StudioToolCards stays as it was: the offer is not one of its cards.
    expect(code(read("components/studio/StudioToolCards.tsx"))).not.toMatch(/SkillOffer|offer/);
  });

  it("the bubble hands the card the real turn", () => {
    const mount = bubble.slice(bubble.indexOf("<SkillOfferCard"), bubble.indexOf("/>", bubble.indexOf("<SkillOfferCard")));
    expect(mount).toContain("messageId: message.id,");
    expect(mount).toContain("streaming: !!message.streaming,");
    expect(mount).toContain("solo: skillOfferOf.solo,");
    expect(mount).toContain("ranByViewer: ranByViewer(message, viewerEmail),");
    expect(mount).toContain("prompt: offerPrompt(skillOfferOf.prompt, viewerEmail),");
    expect(mount).toContain("calls: dedupedToolEvents,");
  });

  it("AgentChat gives solo on proof only, so the dock and a failed room read give no offer", () => {
    const chat = code(read("components/AgentChat.tsx"));
    // With the flag off, `offerSolo` is false at every room read (skillOffer.test.ts).
    expect(chat).toContain("const soloForOffer = offerSolo(skillOfferOn(), { enabled: !compact, room, error: roomError });");
    expect(chat).toContain("{ prompt: promptBefore(visibleMessages, i), solo: soloForOffer }");
    expect(chat).not.toMatch(/solo: !isRoom/);
  });

  it("the live user message keeps the typed mark, and never for a card answer", () => {
    const hook = code(read("hooks/useAgentChat.ts"));
    expect(hook.match(/\.\.\.\(opts\?\.typed === true && !opts\?\.cardAnswer \? \{ typed: true \} : \{\}\)/g)?.length).toBe(2);
  });

  it("the card hands the typed name to the store, and Save reads the store", () => {
    const card = code(read("components/studio/SkillOfferCard.tsx"));
    expect(card).toContain("onName={(next) => setTypedName(offer.messageId, next)}");
    expect(card).toContain("onSave={() => void pressSave(offer, createSkill)}");
    expect(card).toContain("name={boxName(offer)}");
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
    expect(hooks.length).toBe(3);
    expect(Math.max(...hooks)).toBeLessThan(firstReturn);
  });
});
