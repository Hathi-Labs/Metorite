/**
 * Fence for `projects_ai_chat.md` §24.10, the placement rules of a chat card
 * (owner report, 2026-10-23).
 *
 * C3: a card in the thread holds no scroll box. The thread is the one thing
 * that scrolls, and a long body uses `ShowMore`. Two kinds of box are
 * exempt, by file: a live log that follows its own tail (the working trail)
 * and the raw text of an error.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf-8");

/** Every file that draws a card in the chat thread. */
const CARD_FILES = [
  "components/ConfirmationCard.tsx",
  "components/ElicitationCard.tsx",
  "components/GenerativeUINode.tsx",
  "components/Readout.tsx",
  "components/RollupCard.tsx",
  "components/ToolCardShell.tsx",
  "components/genUITemplates.tsx",
  "components/MessageBubble.tsx",
  "components/email/EmailToolCards.tsx",
  "components/projects/ProjectToolCards.tsx",
  "components/tasks/TaskToolCards.tsx",
  "components/crm/CrmEvidence.tsx",
];

/** A class string that caps a height and scrolls inside it. */
const SCROLL_BOX = /["'`][^"'`]*\bmax-h-[^\s"'`]+[^"'`]*\boverflow-(?:y-)?auto\b|["'`][^"'`]*\boverflow-(?:y-)?auto\b[^"'`]*\bmax-h-/;

describe("C3: a chat card holds no scroll box", () => {
  for (const f of CARD_FILES) {
    it(f, () => {
      const hits = src(f)
        .split("\n")
        .map((l, i) => [i + 1, l] as const)
        .filter(([, l]) => SCROLL_BOX.test(l));
      expect(hits.map(([n, l]) => `${f}:${n} ${l.trim()}`)).toEqual([]);
    });
  }

  it("ShowMore cuts with a fade and opens in place, never with a scroll box", () => {
    const s = src("components/ui/ShowMore.tsx");
    expect(s).not.toMatch(/overflow-(y-)?auto/);
    expect(s).toContain('"Show all"');
  });
});

describe("C1: the composer form draws no card", () => {
  const chat = src("components/AgentChat.tsx");
  const form = chat.slice(chat.indexOf("<form onSubmit={handleSubmit}"));

  it("no waiting card and no draft pins above the box", () => {
    expect(form).not.toContain("renderHitlCards()");
    expect(form).not.toMatch(/<(Pinned\w*|Draft\w*Card|ConfirmationCard|ElicitationCard|ConfirmationQueue)\b/);
  });

  it("the Todos panel opens folded on a phone", () => {
    expect(form).toContain("startOpen={!phoneChrome}");
  });
});
