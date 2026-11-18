/**
 * The resting card: what the chat shows when the AI cannot answer (AO-3).
 *
 * Incident 2026-11-17. The AI vendor refused every call, and the member saw
 * an empty reply. The server now names the failure `ai_unavailable`
 * (`acb_llm.run_errors`), and the chat draws a card with a sad elephant, a
 * title, one line and "Try again". These tests hold the card. Each names the
 * mutation it was run against.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AiRestingCardView, ErrorCardView, retryOnce } from "@/components/ChatErrorCard";
import { canRetry, retryPlan } from "@/lib/chatRetry";
import { RUN_ERROR_WORDS, isInfoOnlyNotice, parseStoredRunError } from "@/lib/runErrors";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

/** What the gateway's RUN_ERROR can carry as raw text. The card never shows it. */
const RAW =
  "<class 'agent_framework_openai.OpenAIChatCompletionClient'> service failed: Error code: 502 - " +
  "{'error': {'message': 'the AI Router is unavailable', 'type': 'ConsoleRouterUnavailable'}} " +
  "anthropic credit balance is too low, tier-balanced, deepseek";

const render = (onRetry?: () => void) =>
  renderToStaticMarkup(
    createElement(ErrorCardView, { error: { code: "ai_unavailable", ref: "3f9c2a1b", raw: RAW }, onRetry }),
  );

/** The visible text of the markup, with tags and attributes cut out. */
const textOf = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");

/** The first element in a tree whose props match. */
function findElement(node: ReactNode, match: (props: Record<string, unknown>) => boolean): ReactElement | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = findElement(child, match);
      if (hit) return hit;
    }
    return null;
  }
  if (!isValidElement(node)) return null;
  const props = node.props as Record<string, unknown>;
  if (match(props)) return node;
  return findElement(props.children as ReactNode, match);
}

const tryAgain = (tree: ReactNode) =>
  findElement(tree, (p) => p.children === RUN_ERROR_WORDS.ai_unavailable.action);

describe("the card renders", () => {
  // Mutation: drop the `ai_unavailable` branch of ErrorCardView, and the
  // plain notice draws: no picture, and the button says nothing new.
  it("draws the elephant, the title, the one line and Try again", () => {
    const html = render(() => {});
    expect(html).toContain("data-ai-resting-art");
    expect(html).toContain('data-code="ai_unavailable"');
    expect(html).toContain('role="status"');
    expect(html).toContain("The AI is taking a breather");
    expect(html).toContain("Try again");
    expect(textOf(html)).toContain(RUN_ERROR_WORDS.ai_unavailable.body);
  });

  it("says what happened and what to do, in the owner's shape", () => {
    const { title, body, action, retry, tone } = RUN_ERROR_WORDS.ai_unavailable;
    expect(title).toBe("The AI is taking a breather");
    expect(body).toMatch(/cannot answer right now/);
    expect(body).toMatch(/on our side, not yours/);
    expect(body).toMatch(/message is saved/);
    expect(body).toMatch(/try again in a few minutes/);
    expect(action).toBe("Try again");
    expect(retry).toBe(true);
    expect(tone).toBe("notice");
    // It has a button, so it keeps its card. It is not a slim line.
    expect(isInfoOnlyNotice(RUN_ERROR_WORDS.ai_unavailable)).toBe(false);
  });

  // Mutation: draw the "Show full error" fold, and the raw text leaks.
  it("names no vendor, no tier, no credits and none of our own services", () => {
    const text = textOf(render(() => {})).toLowerCase();
    for (const word of [
      "anthropic", "deepseek", "openai", "claude", "gemini", "credit", "tier", "router",
      "console", "jev", "502", "show full error", "consolerouterunavailable",
    ]) {
      expect(text, word).not.toContain(word);
    }
  });

  it("draws no button when the thread offers no retry", () => {
    expect(render(undefined)).not.toContain("Try again");
  });
});

describe("Try again re-sends the message once", () => {
  // Mutation: let `retryOnce` call `fn` each time, and this fails.
  it("a double press calls the resend once", () => {
    const resend = vi.fn();
    const press = retryOnce(resend);
    press();
    press();
    press();
    expect(resend).toHaveBeenCalledTimes(1);
  });

  // Mutation: wire the button to something other than the card's press.
  it("the button presses the card's own press, and shows disabled once sent", () => {
    const press = vi.fn();
    const open = tryAgain(AiRestingCardView({ onRetry: press }));
    expect(open).not.toBeNull();
    const props = open!.props as { onClick: () => void; disabled?: boolean };
    expect(props.disabled).toBe(false);
    props.onClick();
    expect(press).toHaveBeenCalledTimes(1);

    const after = tryAgain(AiRestingCardView({ onRetry: press, sent: true }));
    expect((after!.props as { disabled?: boolean }).disabled).toBe(true);
  });

  it("the card's press goes through retryOnce", () => {
    const card = read("components/ChatErrorCard.tsx");
    const stateful = card.slice(card.indexOf("function AiRestingCard("));
    expect(stateful).toMatch(/retryOnce\(\(\) => \{\s*setSent\(true\);\s*onRetry\(\);/);
  });

  // Mutation: let retryPlan skip a system error turn, and this fails.
  it("the thread's one retry path re-sends the last message before the card", () => {
    const view = { code: "ai_unavailable", ref: "3f9c2a1b", raw: "" };
    const thread = [
      { id: "u1", role: "user" as const, content: "first question" },
      { id: "a1", role: "assistant" as const, content: "first answer" },
      { id: "u2", role: "user" as const, content: "Plan my week." },
      { id: "e1", role: "system" as const, content: `__ERROR__${JSON.stringify(view)}` },
    ];
    expect(parseStoredRunError(thread[3].content.slice(9)).code).toBe("ai_unavailable");
    expect(canRetry(thread[3])).toBe(true);
    expect(retryPlan(thread, "e1")).toEqual({ resend: "Plan my week.", drop: ["e1", "u2"] });
  });
});

describe("the picture follows the one look", () => {
  const art = read("components/AiRestingArt.tsx");
  const markup = art.slice(art.indexOf("<svg"));

  // Mutation: write `fill="#888"` on the body, and this fails.
  it("writes no colour by hand", () => {
    expect(markup).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(markup).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/);
    expect(markup).not.toMatch(/\b(?:fill|stroke)="(?!none)[^"]*"/);
  });

  it("paints every part with a token class", () => {
    const classes = [...markup.matchAll(/className="([^"]+)"/g)].flatMap((m) => m[1].split(/\s+/));
    const paints = classes.filter((c) => /^(?:fill|stroke)-/.test(c));
    expect(paints.length).toBeGreaterThan(5);
    for (const c of paints) {
      expect(c).toMatch(/^(?:fill-none|(?:fill|stroke)-(?:muted-foreground|primary|card|background)(?:\/\d+)?)$/);
    }
  });

  it("is decoration, hidden from a screen reader", () => {
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).toContain('focusable="false"');
  });

  // Mutation: move the animation out of the no-preference block.
  it("breathes only for a member who did not ask for less motion", () => {
    const css = read("app/globals.css");
    const at = css.indexOf(".ai-rest-breathe {");
    expect(at).toBeGreaterThan(0);
    const block = css.slice(css.lastIndexOf("@media", at), at);
    expect(block).toMatch(/@media \(prefers-reduced-motion: no-preference\) \{\s*$/);
    expect(css.match(/animation: ai-rest-breathe/g)?.length).toBe(1);
    expect(art).toContain('className="ai-rest-breathe"');
  });

  // Mutation: make the animation `infinite`. A failed act never loops.
  it("plays its breath once and does not loop", () => {
    const css = read("app/globals.css");
    const at = css.indexOf(".ai-rest-breathe {");
    const rule = css.slice(at, css.indexOf("}", at));
    expect(rule).toMatch(/animation: ai-rest-breathe 1\.2s ease-in-out 1;/);
    expect(rule).not.toContain("infinite");
  });
});
