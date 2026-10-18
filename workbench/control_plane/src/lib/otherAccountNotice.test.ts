/**
 * The chat's other-account notice has a Switch button when it can (owner
 * call, 2026-10-18). PR #1121 made it a dismissible line, and a dismissal
 * left the member stuck: only the other account can answer the question.
 *
 * The rules, each with the mutation it was run against:
 *
 * 1. This browser holds a slot for that account: a card with a "Switch to
 *    {account}" button, and no Dismiss.
 * 2. No slot, or the switcher flag is off: the info-only line with a
 *    Dismiss, and words that say to sign in from the account menu.
 * 3. That account is active now: the info-only line that says to answer
 *    again, and no switch.
 * 4. A press reads the accounts AGAIN and switches to the slot that holds
 *    the account NOW (review of #1131). A switch in another tab can move an
 *    account to another slot.
 * 5. A switch that throws clears the cover and says why (review of #1131).
 * 6. The switch is the account menu's own (`useSwitchAccount`), with its
 *    cover. No second switch path.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  NO_ACCOUNTS,
  isActiveAccount,
  pressSwitch,
  resetPress,
  slotFor,
  trySwitch,
  type Accounts,
  type OtherAccount,
} from "@/lib/accountSwitch";
import {
  OTHER_ACCOUNT_ACTIVE_WORDS,
  RUN_ERROR_WORDS,
  isInfoOnlyNotice,
  otherAccountSwitchWords,
  type RunErrorView,
} from "@/lib/runErrors";

// The switcher's two hooks, under the test's control. The hooked notice
// calls nothing else, so a test can render it, and call it, with these.
const hooks = vi.hoisted(() => ({
  accounts: null as unknown as Accounts,
  loaded: false,
  switching: null as string | null,
  switchError: null as string | null,
  reload: (async () => null) as unknown as (orgs: boolean) => Promise<Accounts>,
  pick: (async () => {}) as (o: OtherAccount) => Promise<void>,
}));

vi.mock("@/components/AccountSwitcher", () => ({
  useAccounts: () => ({ accounts: hooks.accounts, loaded: hooks.loaded, reload: hooks.reload }),
  useSwitchAccount: () => ({ switching: hooks.switching, error: hooks.switchError, pick: hooks.pick }),
  SwitchingCover: ({ email }: { email: string | null }) => createElement("div", { "data-switching-cover": email }),
}));

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");

const DISMISS = 'aria-label="Dismiss"';
const ERROR: RunErrorView = { code: "answer_in_other_account", ref: null, raw: "alice@a.test" };
const BOB = { email: "bob@b.test", name: "Bob" };
const ALICE: OtherAccount = { slot: 1, email: "Alice@A.test", name: "Alice", organization: null };
const CAROL: OtherAccount = { slot: 2, email: "carol@c.test", name: null, organization: null };
/** What the notice read at mount: bob active, alice in slot 1. */
const ACCOUNTS: Accounts = { enabled: true, active: BOB, others: [ALICE, CAROL] };
/**
 * What a press reads after another tab switched to alice, then to carol.
 * The server swapped the sessions: slot 1 holds bob now, and alice is in
 * slot 2.
 */
const MOVED: Accounts = {
  enabled: true,
  active: { email: "carol@c.test", name: null },
  others: [
    { slot: 1, email: "bob@b.test", name: "Bob", organization: null },
    { slot: 2, email: "alice@a.test", name: "Alice", organization: null },
  ],
};

/** A load of the notice over an empty storage. */
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
  return import("@/components/OtherAccountNotice");
}

type Card = Awaited<ReturnType<typeof load>>;

function renderView(m: Card, accounts: Accounts, extra: Record<string, unknown> = {}) {
  return renderToStaticMarkup(
    createElement(m.OtherAccountNoticeView, { error: ERROR, accounts, onSwitch: () => {}, dismissKey: "k", ...extra }),
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

// The card loads ONCE, here, with room. Its module tree is heavy (the icon
// set), and a fresh import in each test ran past the 5 s test timeout under
// the full suite's load. No test here needs a fresh dismissal store.
let mod: Card;
beforeAll(async () => {
  mod = await load();
}, 60_000);

beforeEach(() => {
  resetPress();
  hooks.accounts = ACCOUNTS;
  hooks.loaded = true;
  hooks.switching = null;
  hooks.switchError = null;
});

afterAll(() => {
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

  // Mutation: drop the `isActiveAccount` check from slotFor, and this fails.
  // A stale list can hold the active account in a slot too.
  it("never names the active account, even when a slot holds it", () => {
    const stale: Accounts = {
      ...ACCOUNTS,
      others: [{ slot: 3, email: "BOB@b.test", name: null, organization: null }, ALICE],
    };
    expect(isActiveAccount(stale, " bob@B.test ")).toBe(true);
    expect(slotFor(stale, "bob@b.test")).toBeNull();
    expect(slotFor(stale, "alice@a.test")).toBe(ALICE);
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
    const html = renderView(mod, ACCOUNTS);
    expect(html).toContain("data-chat-error-card");
    expect(html).toContain("This question is for another account");
    expect(html).toContain("Your answer was not sent. Switch to alice@a.test to answer it.");
    expect(html).toContain("Switch to alice@a.test</span>");
    expect(html).not.toContain(DISMISS);
    expect(html).not.toContain("data-chat-notice-marker");
    expect(html).not.toContain("data-switch-error");
  });

  // Mutation: render the label bare, and a long address pushes the button
  // past the card on a phone.
  it("truncates the label inside the card, and its title holds it all", async () => {
    const html = renderView(mod, ACCOUNTS);
    expect(html).toMatch(/<button[^>]*title="Switch to alice@a\.test"[^>]*class="[^"]*\bmin-w-0 max-w-full\b/);
    expect(html).toContain('<span class="min-w-0 truncate">Switch to alice@a.test</span>');
  });

  // Mutation: drop `onRetry={onSwitch}`, and this fails.
  it("the button calls the press handler", async () => {
    const m = mod;
    const onSwitch = vi.fn();
    const tree = m.OtherAccountNoticeView({ error: ERROR, accounts: ACCOUNTS, onSwitch });
    const card = findElement(tree, (p) => typeof p.onRetry === "function");
    expect(card, "the card has a button handler").not.toBeNull();
    (card!.props as { onRetry: () => void }).onRetry();
    expect(onSwitch).toHaveBeenCalledTimes(1);
  });

  // Mutation: drop the `switchError &&` line, and a failed switch says nothing.
  it("says why the last switch failed", async () => {
    const html = renderView(mod, ACCOUNTS, { switchError: "Could not switch to alice@a.test." });
    expect(html).toMatch(/role="alert"[^>]*data-switch-error[^>]*>Could not switch to alice@a\.test\.<\/p>/);
  });
});

describe("with no slot, the dismissible line", () => {
  // Mutation: give the base words an action, and this fails.
  it("is info-only, with a Dismiss and the sign-in words", async () => {
    const html = renderView(mod, { ...ACCOUNTS, others: [CAROL] });
    expect(isInfoOnlyNotice(RUN_ERROR_WORDS.answer_in_other_account)).toBe(true);
    expect(html).toContain("data-chat-notice-marker");
    expect(html).toContain(DISMISS);
    expect(html).toContain("Sign in to alice@a.test from the account menu to answer it.");
    expect(html).not.toContain("Switch to");
  });

  it("and the same with the switcher off", async () => {
    const html = renderView(mod, NO_ACCOUNTS);
    expect(html).toContain("Sign in to alice@a.test from the account menu to answer it.");
    expect(html).not.toContain("Switch to");
  });
});

describe("with that account active now, the line that says to answer again", () => {
  // Mutation: drop the `isActiveAccount` branch from the view, and this
  // draws the sign-in line instead.
  it("is info-only, has no Switch, and names the account", async () => {
    const html = renderView(mod, {
      enabled: true,
      active: { email: "ALICE@a.test", name: null },
      others: [{ slot: 1, ...BOB, organization: null }],
    });
    expect(isInfoOnlyNotice(OTHER_ACCOUNT_ACTIVE_WORDS)).toBe(true);
    expect(html).toContain("data-chat-notice-marker");
    expect(html).toContain(DISMISS);
    expect(html).toContain("You can answer this question now");
    expect(html).toContain("alice@a.test is the active account now. Answer the question again.");
    expect(html).not.toContain("Switch to");
  });
});

describe("a press reads the accounts again (review of #1131)", () => {
  // Mutation: switch to the slot read at mount, and this switches to bob.
  it("switches to the slot that holds the account NOW", async () => {
    const pick = vi.fn(async () => {});
    expect(await pressSwitch("alice@a.test", async () => MOVED, pick)).toBe("switched");
    expect(pick).toHaveBeenCalledTimes(1);
    expect(pick).toHaveBeenCalledWith(MOVED.others[1]);
  });

  // Mutation: drop the `isActiveAccount` check from pressSwitch, and a stale
  // slot that still names alice is switched to.
  it("does not switch when the account is active already", async () => {
    const fresh: Accounts = { enabled: true, active: { email: "alice@a.test", name: null }, others: [ALICE] };
    const pick = vi.fn(async () => {});
    expect(await pressSwitch("Alice@A.test", async () => fresh, pick)).toBe("active");
    expect(pick).not.toHaveBeenCalled();
  });

  it("does not switch when no slot holds the account now", async () => {
    const pick = vi.fn(async () => {});
    expect(await pressSwitch("alice@a.test", async () => ({ ...ACCOUNTS, others: [CAROL] }), pick)).toBe("signin");
    expect(await pressSwitch("alice@a.test", async () => NO_ACCOUNTS, pick)).toBe("signin");
    expect(pick).not.toHaveBeenCalled();
  });

  // Mutation: drop the `pressing` guard, and a double tap switches twice,
  // which swaps the accounts back.
  it("a second tap during a press does nothing", async () => {
    let answer: (a: Accounts) => void = () => {};
    const slow = () => new Promise<Accounts>((r) => (answer = r));
    const pick = vi.fn(async () => {});
    const first = pressSwitch("alice@a.test", slow, pick);
    expect(await pressSwitch("alice@a.test", async () => ACCOUNTS, pick)).toBe("busy");
    answer(ACCOUNTS);
    expect(await first).toBe("switched");
    expect(pick).toHaveBeenCalledTimes(1);
    // The press is over, so the next one runs.
    expect(await pressSwitch("alice@a.test", async () => ACCOUNTS, pick)).toBe("switched");
  });
});

describe("a switch that throws clears the cover (review of #1131)", () => {
  // Mutation: drop the catch from trySwitch, and the first case rejects.
  it("a network fault is a failure with words, and the slot is not gone", async () => {
    const got = await trySwitch(ALICE, async () => {
      throw new TypeError("Failed to fetch");
    });
    expect(got).toEqual({
      error: "Could not switch to Alice@A.test. Check your connection, then try again.",
      gone: false,
    });
  });

  it("a refused switch means the slot is gone, and a switch that ran is no failure", async () => {
    expect(await trySwitch(ALICE, async () => false)).toEqual({
      error: "Alice@A.test is no longer signed in here. Add it again.",
      gone: true,
    });
    const run = vi.fn(async () => true);
    expect(await trySwitch(ALICE, run)).toBeNull();
    expect(run).toHaveBeenCalledWith(1);
  });

  // A hook cannot run here (vitest is node-env), so the hook's half is read
  // from the source: every failure clears the cover.
  it("the account menu's switch clears the cover on every failure", () => {
    const switcher = read("components/AccountSwitcher.tsx");
    expect(switcher).toMatch(
      /const failed = await trySwitch\(o\);\s*if \(failed\) \{\s*setSwitching\(null\);\s*onCover\?\.\(null\);\s*setError\(failed\.error\);\s*if \(failed\.gone\) onChanged\(\);/,
    );
  });
});

describe("the hooked notice, as the thread mounts it", () => {
  // Mutation: drop `if (!loaded) return null;`, and the first render draws
  // the sign-in line, which then jumps into a card.
  it("draws nothing until the accounts answer, then the state they name", async () => {
    const m = mod;
    const mount = () => renderToStaticMarkup(createElement(m.OtherAccountNotice, { error: ERROR, dismissKey: "k" }));
    hooks.loaded = false;
    hooks.accounts = NO_ACCOUNTS;
    expect(mount()).toBe("");
    hooks.loaded = true;
    hooks.accounts = ACCOUNTS;
    expect(mount()).toContain("Switch to alice@a.test</span>");
    hooks.accounts = { ...ACCOUNTS, others: [CAROL] };
    expect(mount()).toContain("Sign in to alice@a.test from the account menu");
    hooks.accounts = { enabled: true, active: { email: "alice@a.test", name: null }, others: [] };
    expect(mount()).toContain("You can answer this question now");
  });

  it("shows the switcher's cover while it switches, and the switcher's error", async () => {
    const m = mod;
    hooks.switching = "Alice@A.test";
    expect(renderToStaticMarkup(createElement(m.OtherAccountNotice, { error: ERROR }))).toContain(
      'data-switching-cover="Alice@A.test"',
    );
    hooks.switching = null;
    hooks.switchError = "Could not switch to Alice@A.test.";
    expect(renderToStaticMarkup(createElement(m.OtherAccountNotice, { error: ERROR }))).toContain("data-switch-error");
  });

  // Mutation: pass `pick` the slot read at mount, and this switches to bob.
  it("a press reads the accounts again, then picks the fresh slot", async () => {
    const m = mod;
    const reload = vi.fn(async () => MOVED);
    const pick = vi.fn(async () => {});
    hooks.reload = reload;
    hooks.pick = pick;
    // Its only hooks are the two mocked ones, so it can be called directly.
    const tree = m.OtherAccountNotice({ error: ERROR });
    const view = findElement(tree, (p) => typeof p.onSwitch === "function");
    expect(view, "the notice draws the view with a press handler").not.toBeNull();
    (view!.props as { onSwitch: () => void }).onSwitch();
    await vi.waitFor(() => expect(pick).toHaveBeenCalledTimes(1));
    expect(reload).toHaveBeenCalledWith(false);
    expect(pick).toHaveBeenCalledWith(MOVED.others[1]);
  });

  it("does not write a second switch path", () => {
    const notice = read("components/OtherAccountNotice.tsx");
    expect(notice).not.toMatch(/\bswitchTo\(/);
    expect(notice).toContain("useSwitchAccount(");
  });

  // Mutation: drop the branch from MessageBubble, and the thread draws the
  // plain error card, which has no Switch.
  it("the thread mounts it for this code, and the error card does not import the switcher", () => {
    const bubble = read("components/MessageBubble.tsx");
    expect(bubble).toMatch(/if \(runError\.code === "answer_in_other_account"\) \{\s*return \(\s*<OtherAccountNotice/);
    const card = read("components/ChatErrorCard.tsx");
    expect(card).not.toContain("@/components/AccountSwitcher");
  });
});
