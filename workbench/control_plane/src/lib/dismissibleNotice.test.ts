/**
 * An info-only chat notice is a slim line that the member can dismiss
 * (owner report, 2026-10-17, with a screenshot of the amber "The assistant
 * restarted" card): "shouldn't this card be closable? At the moment, it is
 * persistent."
 *
 * The rules, each with the mutation it was run against:
 *
 * 1. A notice with no retry and no action draws as a slim marker with a
 *    Dismiss control, in the muted tones, never the warning card.
 * 2. Dismiss hides it, and the dismissal outlives a remount and a reload,
 *    through the chat's one set of dismissed cards (`lib/dismissedTools.ts`).
 * 3. A browser that refuses storage still draws the notice, and a dismiss
 *    still hides it for the page's life.
 * 4. A notice with an action, and every error, draws no Dismiss.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RUN_ERROR_WORDS, isInfoOnlyNotice, type RunErrorCode } from "@/lib/runErrors";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const CODES = Object.keys(RUN_ERROR_WORDS) as RunErrorCode[];
const INFO_ONLY: RunErrorCode[] = ["run_restarted", "run_restarted_steered", "answer_in_other_account"];
const DISMISS = 'aria-label="Dismiss"';

/** A `localStorage` that keeps its items in a map. */
function memoryStorage() {
  const items = new Map<string, string>();
  return {
    items,
    getItem: (k: string) => items.get(k) ?? null,
    setItem: (k: string, v: string) => void items.set(k, v),
    removeItem: (k: string) => void items.delete(k),
  };
}

/** A `localStorage` that refuses every read and write (a private window). */
const refusingStorage = {
  getItem: () => { throw new Error("SecurityError"); },
  setItem: () => { throw new Error("QuotaExceededError"); },
  removeItem: () => { throw new Error("SecurityError"); },
};

/**
 * A fresh load of the card and the store over this storage: a page reload.
 * The store reads storage once, when its module loads.
 */
async function loadWith(storage: unknown) {
  vi.resetModules();
  vi.stubGlobal("window", { localStorage: storage });
  const card = await import("@/components/ChatErrorCard");
  const store = await import("@/lib/dismissedTools");
  return { ...card, ...store };
}

type Loaded = Awaited<ReturnType<typeof loadWith>>;

const KEY = "notice:s-1:restarted-r1";

function renderNotice(m: Loaded, code: RunErrorCode = "run_restarted", dismissKey: string | undefined = KEY) {
  return renderToStaticMarkup(
    createElement(m.ErrorCardView, { error: { code, ref: null, raw: "" }, onRetry: () => {}, dismissKey }),
  );
}

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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("which notices only inform", () => {
  // Mutation: drop `!words.retry` from isInfoOnlyNotice, and the list grows.
  it("the two restart notices and the other-account notice, and nothing else", () => {
    expect(CODES.filter((c) => isInfoOnlyNotice(RUN_ERROR_WORDS[c])).sort()).toEqual([...INFO_ONLY].sort());
  });

  // Mutation: drop `!words.action`, and this fails. No code today has an
  // action without a retry, so the list above cannot see that clause.
  it("a notice with an action is never info-only, with or without a retry", () => {
    expect(isInfoOnlyNotice({ title: "t", body: "b", retry: false, tone: "notice", action: "Open" })).toBe(false);
    expect(isInfoOnlyNotice({ title: "t", body: "b", retry: false })).toBe(false);
    expect(isInfoOnlyNotice({ title: "t", body: "b", retry: false, tone: "notice" })).toBe(true);
  });
});

describe("an info-only notice is a slim line with a Dismiss", () => {
  // Mutation: return the old card for every notice, and these fail.
  it.each(INFO_ONLY)("%s", async (code) => {
    const m = await loadWith(memoryStorage());
    const html = renderNotice(m, code);
    expect(html).toContain("data-chat-notice-marker");
    expect(html).toContain('role="status"');
    expect(html).toContain(DISMISS);
    expect(html).toContain(RUN_ERROR_WORDS[code].title);
    // The muted tones of statusAccent, and no warning ramp.
    expect(html).toContain("bg-muted/40");
    expect(html).toContain("text-muted-foreground");
    expect(html).not.toMatch(/\b(?:bg|border|text)-warning\b/);
    expect(html).not.toContain("data-chat-error-card");
  });

  it("the body is the second part of the same line", async () => {
    const m = await loadWith(memoryStorage());
    const html = renderNotice(m, "run_restarted_steered");
    expect(html).toMatch(/The assistant restarted<\/span>.*Your answer went into the current reply\.<\/span>/);
    expect(renderNotice(m, "answer_in_other_account")).toContain("Switch to the account that asked to answer it.");
  });
});

describe("Dismiss hides the notice, and the dismissal stays", () => {
  // Mutation: drop `onClick={onDismiss}` from the Dismiss Button, and the
  // first test fails.
  it("the Dismiss button calls its handler", async () => {
    const m = await loadWith(memoryStorage());
    const onDismiss = vi.fn();
    const tree = m.InfoNoticeView({ title: "T", body: "B", onDismiss });
    const button = findElement(tree, (p) => p["aria-label"] === "Dismiss");
    expect(button, "the marker draws a Dismiss control").not.toBeNull();
    (button!.props as { onClick: () => void }).onClick();
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("the handler writes the notice's key to the one dismissed set", () => {
    // Mutation: drop `dismissToolCard(dismissKey)` from the handler, and this
    // fails. A click cannot run here (vitest is node-env), so the wiring is
    // read from source, and the store's half is tested below.
    const card = read("components/ChatErrorCard.tsx");
    expect(card).toMatch(/onDismiss=\{\(\) => \{\s*setHidden\(true\);\s*if \(dismissKey\) dismissToolCard\(dismissKey\);/);
    const bubble = read("components/MessageBubble.tsx");
    expect(bubble).toContain("dismissKey={noticeDismissKey(sessionId, message.id)}");
  });

  // Mutation: drop the `dismissed.has(dismissKey)` check, and this fails.
  it("a dismissed key draws nothing, after a remount and after a reload", async () => {
    const storage = memoryStorage();
    let m = await loadWith(storage);
    expect(m.noticeDismissKey("s-1", "restarted-r1")).toBe(KEY);
    expect(renderNotice(m)).toContain(DISMISS);
    m.dismissToolCard(KEY);
    // A remount in the same page.
    expect(renderNotice(m)).toBe("");
    // Another notice, or the same message in another session, still draws.
    expect(renderNotice(m, "run_restarted", "notice:s-2:restarted-r1")).toContain(DISMISS);
    // A reload reads the key back from storage.
    m = await loadWith(storage);
    expect(renderNotice(m)).toBe("");
  });

  // Mutation: let `load()` or `persist()` rethrow, and this fails.
  it("a browser that refuses storage still draws, and still dismisses for the page", async () => {
    const m = await loadWith(refusingStorage);
    expect(renderNotice(m)).toContain(DISMISS);
    expect(() => m.dismissToolCard(KEY)).not.toThrow();
    expect(renderNotice(m)).toBe("");
  });
});

describe("a notice with an action, and an error, keeps its card", () => {
  // Mutation: make every `notice` tone info-only, and Continue and Open
  // activity lose their card.
  it.each(CODES.filter((c) => !INFO_ONLY.includes(c)))("%s draws no Dismiss", async (code) => {
    const m = await loadWith(memoryStorage());
    const html = renderNotice(m, code);
    expect(html).not.toContain(DISMISS);
    expect(html).not.toContain("data-chat-notice-marker");
    expect(html).toContain("data-chat-error-card");
  });
});
