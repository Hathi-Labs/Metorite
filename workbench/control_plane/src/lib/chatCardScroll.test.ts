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
  // STU-F5a: the live records card of a Studio read.
  "components/studio/StudioRecordsCard.tsx",
  // STU-F6a: the skill offer of a finished turn.
  "components/studio/SkillOfferCard.tsx",
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

  it("§24.11 (WD-1): the dock's body is its one scroll box, and it holds no other", () => {
    const dock = src("components/WaitingDock.tsx");
    expect(dock.match(/overflow-y-auto/g)?.length).toBe(1);
    expect(dock).toMatch(/data-dock-body=""[^>]*?className="overflow-y-auto/);
    // The fold of the dock's queue adds no scroll box to the queue.
    expect(src("components/ConfirmationQueue.tsx")).not.toMatch(/overflow-(y-)?auto/);
  });

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

  it("§24.11 (WD-1): with the flag on, the dock sits between the thread and the form, never inside it", () => {
    // A button of a card inside the form is a submit button: Enter in a card
    // field sent the message, and an option tap sent the box (verifier P0).
    expect(form).not.toMatch(/<WaitingDock\b/);
    const before = chat.slice(0, chat.indexOf("<form onSubmit={handleSubmit}"));
    const host = before.slice(before.lastIndexOf("<div ref={dockHostRef}"));
    expect(host).toContain("<WaitingDock count={dockN}");
    expect(host).toContain("{showDock && (");
    expect(host).toContain("{renderDockCards}");
    expect(chat).toContain("const showDock = dockShown(dockOn, dockSources);");
    // Flag off: the thread draws the cards as before.
    expect(chat).toContain("if (dockOn) return renderDockThreadPart();");
  });

  it("the Todos panel opens folded on a phone", () => {
    expect(form).toContain("startOpen={!phoneChrome}");
  });
});
