/**
 * The chat's other-account notice has a Switch button when it can (owner
 * call, 2026-10-18). PR #1121 made it a dismissible line, and a dismissal
 * left the member stuck: only the other account can answer the question.
 *
 * The rules, each with the mutation it was run against:
 *
 * 1. This browser holds a slot for that account: a card with a "Switch to
 *    {account}" button, and no Dismiss. The button switches to that slot.
 * 2. No slot, or the switcher flag is off: the info-only line with a
 *    Dismiss, and words that say to sign in from the account menu.
 * 3. The switch is the account menu's own (`useSwitchAccount`, then
 *    `switchTo`), with its cover. No second switch path.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NO_ACCOUNTS, slotFor, type Accounts, type OtherAccount } from "@/lib/accountSwitch";
import { RUN_ERROR_WORDS, isInfoOnlyNotice, otherAccountSwitchWords, type RunErrorView } from "@/lib/runErrors";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const DISMISS = 'aria-label="Dismiss"';
const ERROR: RunErrorView = { code: "answer_in_other_account", ref: null, raw: "alice@a.test" };
const ALICE: OtherAccount = { slot: 2, email: "Alice@A.test", name: "Alice", organization: null };
const ACCOUNTS: Accounts = {
  enabled: true,
  active: { email: "bob@b.test", name: "Bob" },
  others: [{ slot: 1, email: "carol@c.test", name: null, organization: null }, ALICE],
};

/** A fresh load of the card over an empty storage. */
async function load() {
  vi.resetModules();
  const items = new Map<string, string>();
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (k: string) => items.get(k) ?? null,
      setItem: (k: string, v: string) => void items.set(k, v),
      removeItem: (k: string) => void items.delete(k),
    },
  });
  return import("@/components/ChatErrorCard");
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

describe("the slot for the notice's account", () => {
  // Mutation: compare without lower-casing, and the first case fails.
  it("matches the address with no regard to case or spaces", () => {
    expect(slotFor(ACCOUNTS, " alice@a.test ")).toBe(ALICE);
    expect(slotFor(ACCOUNTS, "dave@d.test")).toBeNull();
    expect(slotFor(ACCOUNTS, "")).toBeNull();
  });

  // Mutation: drop the `enabled` check, and this fails.
  it("is null with the switcher off", () => {
    expect(slotFor(NO_ACCOUNTS, "alice@a.test")).toBeNull();
    expect(slotFor({ ...ACCOUNTS, enabled: false }, "alice@a.test")).toBeNull();
  });

  it("never names the active account, which is not a switch", () => {
    expect(slotFor(ACCOUNTS, "bob@b.test")).toBeNull();
  });
});

describe("with a slot, a card with a Switch button", () => {
  // Mutation: drop the `action` from otherAccountSwitchWords, and the notice
  // is info-only again.
  it("is never info-only, and the button carries the address", () => {
    const words = otherAccountSwitchWords(" alice@a.test ");
    expect(isInfoOnlyNotice(words)).toBe(false);
    expect(words.action).toBe("Switch to alice@a.test");
  });

  it("draws the card, the Switch button and no Dismiss", async () => {
    const m = await load();
    const html = renderToStaticMarkup(
      createElement(m.OtherAccountNoticeView, { error: ERROR, target: ALICE, onSwitch: () => {}, dismissKey: "k" }),
    );
    expect(html).toContain("data-chat-error-card");
    expect(html).toContain("This question is for another account");
    expect(html).toContain("Your answer was not sent. Switch to alice@a.test to answer it.");
    expect(html).toContain("Switch to alice@a.test</span>");
    expect(html).not.toContain(DISMISS);
    expect(html).not.toContain("data-chat-notice-marker");
  });

  // Mutation: render the label bare, and a long address pushes the button
  // past the card on a phone.
  it("truncates the label inside the card, and its title holds it all", async () => {
    const m = await load();
    const html = renderToStaticMarkup(
      createElement(m.OtherAccountNoticeView, { error: ERROR, target: ALICE, onSwitch: () => {} }),
    );
    expect(html).toMatch(/<button[^>]*title="Switch to alice@a\.test"[^>]*class="[^"]*\bmin-w-0 max-w-full\b/);
    expect(html).toContain('<span class="min-w-0 truncate">Switch to alice@a.test</span>');
  });

  // Mutation: pass `onSwitch` the first slot, or none, and this fails.
  it("the button switches to that slot", async () => {
    const m = await load();
    const onSwitch = vi.fn();
    const tree = m.OtherAccountNoticeView({ error: ERROR, target: ALICE, onSwitch });
    const card = findElement(tree, (p) => typeof p.onRetry === "function");
    expect(card, "the card has a button handler").not.toBeNull();
    (card!.props as { onRetry: () => void }).onRetry();
    expect(onSwitch).toHaveBeenCalledWith(ALICE);
  });
});

describe("with no slot, the dismissible line", () => {
  // Mutation: give the base words an action, and this fails.
  it("is info-only, with a Dismiss and the sign-in words", async () => {
    const m = await load();
    const html = renderToStaticMarkup(
      createElement(m.OtherAccountNoticeView, { error: ERROR, target: null, onSwitch: () => {}, dismissKey: "k" }),
    );
    expect(isInfoOnlyNotice(RUN_ERROR_WORDS.answer_in_other_account)).toBe(true);
    expect(html).toContain("data-chat-notice-marker");
    expect(html).toContain(DISMISS);
    expect(html).toContain("Sign in to alice@a.test from the account menu to answer it.");
    expect(html).not.toContain("Switch to");
  });
});

describe("the switch is the account menu's own", () => {
  // Mutation: call `switchTo` straight from the card, or drop the cover, and
  // this fails. A click cannot run here (vitest is node-env), so the wiring
  // is read from the source.
  it("goes through useSwitchAccount, with the cover, and waits for the first read", () => {
    const card = read("components/ChatErrorCard.tsx");
    expect(card).toContain('if (error.code === "answer_in_other_account") {');
    expect(card).toContain("const { switching, pick } = useSwitchAccount(() => void reload(false));");
    expect(card).toContain("target={slotFor(accounts, error.raw)}");
    expect(card).toContain("onSwitch={(o) => void pick(o)}");
    expect(card).toContain("{switching && <SwitchingCover email={switching} />}");
    expect(card).toContain("if (!loaded) return null;");
    expect(card).not.toMatch(/\bswitchTo\(/);
    const switcher = read("components/AccountSwitcher.tsx");
    expect(switcher).toContain("export function useSwitchAccount(");
    expect(switcher).toMatch(/if \(!\(await switchTo\(o\.slot\)\)\)/);
  });
});
