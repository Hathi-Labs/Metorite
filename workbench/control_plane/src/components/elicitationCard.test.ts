/**
 * The question card of `ask_questions` and `ask_user` (owner reports,
 * 2026-10-09 and 2026-10-13).
 *
 * Mutations caught, by test:
 * - the 💬 emoji comes back as the header icon;
 * - the title goes back to the question's own `header` (a fragment);
 * - the question is clipped by a class (`truncate`, `line-clamp-*`), or cut
 *   by a `slice`;
 * - the recommended mark goes back to a star, or leaves the shared badge;
 * - an option goes back to a hand-made selected fill instead of `Button`'s
 *   `selected`;
 * - Submit is on before a choice;
 * - the answered card keeps its Submit.
 *
 * The 200 and 409 click paths run in a real browser in
 * `e2e/genui-option-picker.spec.ts`, and the payload is held by
 * `src/lib/elicitationAnswer.test.ts`.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import ElicitationCard, { ANSWERED_TITLE, QUESTION_ICON, questionsTitle } from "./ElicitationCard";

/** Longer than the old server cap of 200, as the owner's question was. */
const LONG_Q =
  "Do you want me to archive #22 together with #23? Both ask for the same change: #22 «Move the assistant " +
  "button to the top bar» and #23 «The assistant button should be in the same place on every page, so a member can always find it».";

const QUESTIONS = [{
  header: "#23 with #22?",
  question: LONG_Q,
  allowFreeformInput: false,
  options: [
    { label: "Archive both #22 and #23", recommended: true },
    { label: "Archive only #22" },
  ],
}];

const render = (props: Partial<Parameters<typeof ElicitationCard>[0]> = {}) =>
  renderToStaticMarkup(createElement(ElicitationCard, { questions: QUESTIONS, onSubmit: () => {}, ...props }));

const html = render();
const src = readFileSync(fileURLToPath(new URL("./ElicitationCard.tsx", import.meta.url)), "utf8");

/** The markup of the element that carries `attr`, up to its close. */
function element(markup: string, attr: string): string {
  const at = markup.indexOf(attr);
  const start = markup.lastIndexOf("<", at);
  return markup.slice(start, markup.indexOf(">", at) + 1);
}

describe("ElicitationCard: one look with the other chat cards", () => {
  it("draws no emoji, and its icon comes from the app set", () => {
    expect(html).not.toMatch(/\p{Extended_Pictographic}/u);
    expect(QUESTION_ICON).toBe("MessageCircleQuestion");
    // The header icon is an svg from `<Icon>`, inside the shared title row.
    expect(html).toMatch(/<svg[^>]*lucide[^>]*>[\s\S]*?data-rollup-title/);
  });

  it("titles itself with a short label, never a part of the question", () => {
    expect(questionsTitle(1)).toBe("Question");
    expect(questionsTitle(3)).toBe("3 questions");
    const title = /data-rollup-title=""[^>]*><span[^>]*>([^<]*)</.exec(html)?.[1];
    expect(title).toBe("Question");
    expect(html).not.toContain(">#23 with #22?<");
  });

  it("shows the whole question, and no class clips it", () => {
    const shown = html.replace(/<[^>]+>/g, "");
    expect(shown).toContain("Do you want me to archive #22 together with #23?");
    expect(shown).toContain("so a member can always find it");
    expect(shown).not.toContain("«");
    const p = element(html, "data-elicitation-question");
    expect(p).not.toMatch(/truncate|line-clamp/);
    // A long «name» and its full stop wrap like words. The pill group's
    // `whitespace-nowrap` ran the owner's title past the card's edge.
    expect(html).toContain("data-fenced-name");
    expect(html).not.toContain('class="whitespace-nowrap"');
    expect(p).toContain("whitespace-pre-wrap");
    // The card never cuts the text itself: the server cuts at a word.
    expect(src).not.toMatch(/question\.(slice|substring)\(/);
  });

  it("marks the recommended option with the shared badge, and no star", () => {
    expect(html).toContain(">Recommended<");
    expect(html).not.toMatch(/[★⭐☆]/);
    expect(html).not.toContain("lucide-star");
    expect(src).toContain("<RecommendedBadge />");
  });

  it("draws each option as a dense row of the shared Button toggle", () => {
    expect(html.match(/data-elicitation-option=""/g)?.length).toBe(2);
    expect(html.match(/aria-pressed="false"/g)?.length).toBe(2);
    expect(html).not.toContain("bg-primary/20");
    expect(html).not.toMatch(/rounded-full[^"]*border/);
  });

  it("keeps Submit off until a choice is made, on the shared primary Button", () => {
    const submit = element(html, "data-elicitation-submit");
    expect(submit).toMatch(/\sdisabled=""/);
    expect(submit).toContain("bg-primary text-primary-foreground");
  });

  it("draws an answered card as answered, with no Submit and no box", () => {
    const done = render({ answered: "Archive both #22 and #23" });
    expect(done).toContain('data-elicitation-card="answered"');
    expect(done).toContain(`>${ANSWERED_TITLE}<`);
    expect(done).toContain("Archive both #22 and #23");
    expect(done).not.toContain("data-elicitation-submit");
    expect(done).not.toContain("<textarea");
  });
});
