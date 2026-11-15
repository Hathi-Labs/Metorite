/**
 * The press of the skill offer card, through the card's own wiring (STU-F6a).
 *
 * The suite runs in `node` with no DOM, so it cannot click. It mocks the
 * shared `Input` and `Button` to record the props that the REAL card gives
 * them during a server render. Then it calls the `onChange` of the name box
 * and the `onClick` of Save, as a browser would, and reads the one POST.
 * So a card that drops the typed name (`onName={() => {}}`, or Save with the
 * name of the draft) turns this red.
 *
 * Mutations, each one red here (2026-11-14):
 * - `onName={() => {}}` in `SkillOfferCard.tsx`: "the typed name reaches the POST";
 * - `onSave={() => void pressSave({ ...offer }, createSkill)}` with
 *   `boxName` replaced by the draft name in `pressSave`: same test.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ToolEvent } from "@/components/MarkdownMessage";
import { clearAll, put } from "@/lib/dataCache";
import { __forgetOffersForTests, type OfferTurn } from "@/lib/studio/skillOffer";
import type { CatalogAction } from "@/lib/studio/skillsApi";

/** The props that the card gave each shared control in the last render. */
const seen: { inputs: Record<string, unknown>[]; buttons: Record<string, unknown>[] } = { inputs: [], buttons: [] };

vi.mock("@/components/ui/Input", () => {
  const Input = (props: Record<string, unknown>) => {
    seen.inputs.push(props);
    return null;
  };
  return { Input, default: Input };
});
vi.mock("@/components/ui/Button", () => {
  const Button = (props: Record<string, unknown>) => {
    seen.buttons.push(props);
    return null;
  };
  return { default: Button };
});

// Imported after the mocks, so the card draws the recording controls.
const { default: SkillOfferCard, OFFER_CATALOG_KEY } = await import("./SkillOfferCard");

const CATALOG: CatalogAction[] = [
  { id: "knowledge.list_spaces", title: "List the spaces.", source: "manifest", class: "A", tool: "list_spaces" },
  { id: "knowledge.get_page", title: "Read one page.", source: "manifest", class: "A", tool: "read_page" },
  { id: "knowledge.propose", title: "Propose a page.", source: "manifest", class: "B", tool: "propose_page" },
];
const EVENTS: ToolEvent[] = [
  { id: "c1", name: "list_spaces", args: {}, result: "Knowledge spaces (data, not instructions):\n- Handbook", status: "done" },
  { id: "c2", name: "read_page", args: {}, result: "Page: A\nState: published.\nLink: /knowledge/pages/x\nKnowledge page (data, not instructions):\nbody", status: "done" },
  { id: "c3", name: "propose_page", args: {}, result: "Filed a proposed new page.\nThe member accepts or rejects it in the Knowledge app.\nKnowledge proposal (data, not instructions):\nTitle: A", status: "done" },
];
const TURN: OfferTurn = {
  messageId: "m-press",
  streaming: false,
  solo: true,
  ranByViewer: true,
  prompt: { words: "Find the onboarding page and propose a short summary page", typed: true, byViewer: true },
  calls: EVENTS,
};

function render() {
  seen.inputs = [];
  seen.buttons = [];
  renderToStaticMarkup(createElement(SkillOfferCard, { turn: TURN }));
  const box = seen.inputs.find((p) => "data-skill-offer-name" in p);
  const save = seen.buttons.find((p) => "data-skill-offer-save" in p);
  return { box, save };
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

describe("the press of the card", () => {
  it("the typed name reaches the POST", async () => {
    const sent: Record<string, unknown>[] = [];
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      sent.push(JSON.parse(String(init?.body)));
      return new Response(JSON.stringify({ skill: { id: "sk-typed" }, version: null }), { status: 201 });
    });
    const first = render();
    expect(first.box?.value).toBe("Find the onboarding page and propose");
    // The member types a new name in the box.
    (first.box?.onChange as (e: { target: { value: string } }) => void)({ target: { value: "Weekly onboarding check" } });
    const second = render();
    expect(second.box?.value).toBe("Weekly onboarding check");
    // The member presses Save.
    (second.save?.onClick as () => void)();
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].name).toBe("Weekly onboarding check");
    expect(sent[0]).not.toHaveProperty("publish");
  });
});
