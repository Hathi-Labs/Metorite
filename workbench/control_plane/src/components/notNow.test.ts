/**
 * The one "Not now" control of every card that parks a run (owner request,
 * 2026-10-18). `components/NotNow.tsx` draws it, and
 * `lib/declineAnswer.ts` holds the envelope.
 *
 * Mutations caught, by test:
 * - "Not now" becomes a filled or a destructive button, or the X loses its
 *   accessible name;
 * - a declined card draws as an error, or keeps a live Submit or Not now;
 * - a card that parks no run (no `onDecline`) draws Not now;
 * - a card family draws its own "Not now" instead of the shared one;
 * - the plan card's Cancel stops sending the decline of a parked card;
 * - the chat sends a decline that no run took as a chat message.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { DeclinedNote, NotNowButton, NotNowClose } from "./NotNow";
import { TEMPLATE_REGISTRY } from "./genUITemplates";
import { DECLINED_ANSWER, DECLINED_LINE } from "@/lib/declineAnswer";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const render = (el: (props: any) => React.ReactElement, props: Record<string, unknown> = {}) =>
  renderToStaticMarkup(createElement(el, props));
const tpl = (name: string, data: Record<string, unknown>, ctx?: Record<string, unknown>) =>
  renderToStaticMarkup(createElement(() => TEMPLATE_REGISTRY[name](data, ctx as never)));

/** The markup of the element that carries `attr`, up to its close. */
function element(markup: string, attr: string): string {
  const at = markup.indexOf(attr);
  const start = markup.lastIndexOf("<", at);
  return markup.slice(start, markup.indexOf(">", at) + 1);
}

describe("NotNow: the shared parts", () => {
  it("is a quiet button, never a filled or a destructive one", () => {
    const html = render(NotNowButton, { onClick: () => {} });
    expect(html).toContain(">Not now<");
    const btn = element(html, "data-not-now");
    expect(btn).toContain("cc-control");
    expect(btn).not.toMatch(/bg-primary|destructive/);
  });

  it("puts an X in the title row, named Not now", () => {
    const btn = element(render(NotNowClose, { onClick: () => {} }), "data-not-now-close");
    expect(btn).toContain('aria-label="Not now"');
    expect(btn).toContain('title="Not now"');
  });

  it("says Not answered as a muted status, never as an error", () => {
    const html = render(DeclinedNote);
    expect(html).toContain('role="status"');
    expect(html).toContain(DECLINED_LINE);
    expect(html).toContain("text-muted-foreground");
    expect(html).not.toMatch(/destructive|warning|role="alert"/);
  });
});

describe("the form card and the option picker", () => {
  const form = { title: "Edit #4", submitLabel: "Review changes", fields: [{ name: "title", label: "Title", value: "x" }] };
  const picker = { title: "How?", options: [{ id: "a", label: "A" }, { id: "b", label: "B" }] };
  const live = { onAction: () => {}, onDecline: () => {} };

  it("draws Not now beside Submit, and the X, on a card that parks a run", () => {
    for (const html of [tpl("formCard", form, live), tpl("optionPicker", picker, live)]) {
      expect(html).toContain("data-not-now=\"\"");
      expect(html).toContain("data-not-now-close");
    }
    expect(tpl("formCard", form, live)).toContain(">Review changes<");
  });

  it("draws no Not now on a card that parks nothing", () => {
    for (const html of [tpl("formCard", form, { onAction: () => {} }), tpl("optionPicker", picker, { onAction: () => {} })]) {
      expect(html).not.toContain("data-not-now");
    }
  });

  it("locks a declined card, and says Not answered after a remount", () => {
    for (const html of [
      tpl("formCard", form, { ...live, answered: DECLINED_ANSWER }),
      tpl("optionPicker", picker, { ...live, answered: DECLINED_ANSWER }),
    ]) {
      expect(html).toContain("data-declined");
      expect(html).toContain("Not answered");
      expect(html).not.toContain("data-not-now");
      expect(html).not.toContain(">Sent<");
    }
    expect(tpl("formCard", form, { ...live, answered: DECLINED_ANSWER })).not.toContain(">Review changes<");
    // Every option of a declined picker is off.
    const opts = tpl("optionPicker", picker, { ...live, answered: DECLINED_ANSWER }).match(/<button[^>]*aria-pressed[^>]*>/g) ?? [];
    expect(opts.length).toBe(2);
    expect(opts.every((b) => b.includes("disabled"))).toBe(true);
  });

  it("goes through one gate, so a pick and Not now cannot both fire", () => {
    const src = read("./genUITemplates.tsx");
    expect(src.match(/exit\.current\.submit\(/g)?.length).toBe(2);
    expect(src.match(/exit\.current\.decline\(/g)?.length).toBe(2);
  });
});

describe("the plan card", () => {
  const plan = { project: { name: "Steps" }, tasks: [{ key: "t1", title: "One" }] };

  it("draws a declined plan as cancelled after a remount", () => {
    const html = tpl("planCard", plan, { onAction: () => {}, onDecline: () => {}, answered: DECLINED_ANSWER });
    expect(html).toContain('data-plan-done="cancelled"');
  });

  it("sends the decline from Cancel when the card parks a run", () => {
    const src = read("./PlanCardBody.tsx");
    expect(src).toMatch(/if \(onDecline\) onDecline\(\);\s*else onAction\?\.\(PLAN_CANCEL\);/);
  });
});

describe("one control for every card family", () => {
  it("draws Not now only through components/NotNow.tsx", () => {
    for (const file of ["./ElicitationCard.tsx", "./genUITemplates.tsx", "./PlanCardBody.tsx", "./AgentChat.tsx"]) {
      expect(read(file), file).not.toMatch(/>\s*Not now\s*</);
    }
    expect(read("./ElicitationCard.tsx")).toContain('from "@/components/NotNow"');
    expect(read("./genUITemplates.tsx")).toContain('from "@/components/NotNow"');
  });

  it("never sends a decline as a chat message", () => {
    const chat = read("./AgentChat.tsx");
    // Each decline posts the envelope, with a restore and no fallback message.
    const declines = chat.match(/postRespondInput\(declineBody\([^)]*\)\)?,[^;]*;/g) ?? [];
    expect(declines.length).toBe(3);
    for (const call of declines) expect(call).not.toContain("submitText");
  });
});
