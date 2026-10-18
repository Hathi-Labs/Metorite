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

import { DECLINED_NOTE, DECLINED_TITLE } from "@/lib/declineAnswer";
import ElicitationCard, {
  ANSWERED_TITLE,
  NEEDS_ANSWER,
  OPTIONAL_MARK,
  QUESTION_ICON,
  questionsTitle,
} from "./ElicitationCard";

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

// Owner report, 2026-10-17: "even after selecting, i cant submit." A pick
// did count, but the text question above it had no answer, and the card
// did not say so. The click path (a pick, then the reason and the mark,
// then text, then Submit) runs in a real browser in
// `e2e/genui-option-picker.spec.ts`. The rules are in
// `src/lib/elicitationAnswer.test.ts`.
// Mutations caught: the reason line goes, Submit loses its
// `aria-describedby`, the mark shows on a card with no answer, and an
// optional question loses its mark.
describe("ElicitationCard: Submit never blocks without a reason", () => {
  const OWNER = [
    {
      header: "Target deadline",
      question: "What date should the system be live by?",
      allowFreeformInput: true,
      options: null,
    },
    {
      header: "Task owners",
      question: "Who should own the plan tasks?",
      allowFreeformInput: true,
      options: [{ label: "Propose from the team", recommended: true }, { label: "I'll give owners" }],
    },
  ];

  it("names the questions that block Submit, and Submit points at that line", () => {
    const two = render({ questions: OWNER });
    const submit = element(two, "data-elicitation-submit");
    expect(submit).toMatch(/\sdisabled=""/);
    const describedBy = /aria-describedby="([^"]+)"/.exec(submit)?.[1];
    expect(describedBy).toBeTruthy();
    const reason = element(two, "data-elicitation-blocked");
    expect(reason).toContain(`id="${describedBy}"`);
    expect(reason).toContain('aria-live="polite"');
    expect(two).toContain(">Answer Target deadline and Task owners to submit.<");
  });

  it("marks no question on a card with no answer yet", () => {
    const two = render({ questions: OWNER });
    expect(two).not.toContain(NEEDS_ANSWER);
    expect(two).not.toContain("data-elicitation-unanswered");
  });

  it("says 'the question' on a card of one question", () => {
    expect(html).toContain(">Answer the question to submit.<");
  });

  it("marks an optional question, and does not count it", () => {
    const optional = render({ questions: [{ ...OWNER[0], required: false }, OWNER[1]] });
    expect(optional).toContain(`· ${OPTIONAL_MARK}`);
    expect(optional).toContain(">Answer Task owners to submit.<");
  });

  it("draws no reason and no mark on an answered card", () => {
    const done = render({ questions: OWNER, answered: "Target deadline: 1 Dec" });
    expect(done).not.toContain("data-elicitation-blocked");
    expect(done).not.toContain(OPTIONAL_MARK);
  });
});

// Owner request, 2026-10-18: "if I don't want to answer a question for some
// reason, I should be allowed to turn off the card." The envelope and the
// gate are in `src/lib/declineAnswer.test.ts`, and the shared control in
// `src/components/notNow.test.ts`.
// Mutations caught: Not now shows on a card that parks no run, the X loses
// its name, a declined card keeps Submit, Not now or the box, it draws as an
// error, or Submit and Not now stop sharing the one gate.
describe("ElicitationCard: Not now", () => {
  it("draws Not now beside Submit and an X in the title row", () => {
    const live = render({ onDecline: () => {} });
    expect(live).toContain('data-not-now=""');
    expect(element(live, "data-not-now-close")).toContain('aria-label="Not now"');
    // Submit comes first, then Not now.
    expect(live.indexOf("data-elicitation-submit")).toBeLessThan(live.indexOf('data-not-now=""'));
  });

  it("draws no Not now on a card that parks no run", () => {
    expect(html).not.toContain("data-not-now");
  });

  it("locks a declined card, and says Not answered in muted words", () => {
    const done = render({ onDecline: () => {}, declined: true });
    expect(done).toContain('data-elicitation-card="declined"');
    expect(done).toContain(`>${DECLINED_TITLE}<`);
    expect(done).toContain(DECLINED_NOTE);
    expect(done).not.toContain("data-elicitation-submit");
    expect(done).not.toContain("data-not-now");
    expect(done).not.toContain("<textarea");
    expect(done).not.toMatch(/destructive|role="alert"/);
    // Each option is off.
    const opts = done.match(/<button[^>]*data-elicitation-option[^>]*>/g) ?? [];
    expect(opts.length).toBe(2);
    expect(opts.every((b) => b.includes("disabled"))).toBe(true);
  });

  it("sends Submit and Not now through one gate, so only the first fires", () => {
    expect(src).toMatch(/exit\.current\.submit\(\(\) => onSubmit\(/);
    expect(src).toMatch(/exit\.current\.decline\(onDecline\)/);
  });
});
