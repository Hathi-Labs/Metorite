// ACC-2 (task 3988): the wiring of the two writes of "Who can use". The rules
// are pure and `lib/accessSave.test.ts` holds them. This file drives the real
// components, so a component that skips a rule fails here. The runner has no
// DOM, so a small hook store runs a component as a function. It reads the
// element tree and calls the handlers. The fetch is a stub.
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { clearAll, put } from "@/lib/dataCache";

import { ACCESS_KEY } from "../lib/accessSave";
import type { Connection } from "../lib/connections";
import { row } from "../lib/testRows";
import { ACCESS_WAIT_MS, CLEAR_CHANGED, RULE_GONE, type AccessItem, type AccessList } from "../lib/whoCanUse";
import ConnectionList from "./ConnectionList";
import ConnectionsView from "./ConnectionsView";
import WhoCanUseDialog, { UnmatchedRules, WhoCanUseBody, type WhoCanUseBodyProps } from "./WhoCanUse";

const hooks = vi.hoisted(() => ({
  cells: [] as unknown[],
  at: 0,
  dirty: false,
  effects: [] as Array<() => void>,
  rules: null as unknown,
  rulesError: null as string | null,
  rows: [] as unknown,
}));

vi.mock("react", async (importOriginal) => {
  const real = await importOriginal<typeof import("react")>();
  function useState(initial: unknown) {
    const i = hooks.at++;
    if (!(i in hooks.cells)) hooks.cells[i] = typeof initial === "function" ? (initial as () => unknown)() : initial;
    const set = (next: unknown) => {
      const value = typeof next === "function" ? (next as (v: unknown) => unknown)(hooks.cells[i]) : next;
      if (Object.is(value, hooks.cells[i])) return;
      hooks.cells[i] = value;
      hooks.dirty = true;
    };
    return [hooks.cells[i], set];
  }
  const useRef = (initial: unknown) => useState(() => ({ current: initial }))[0];
  // An effect runs after a settled render, and only when its deps moved.
  function useEffect(run: () => void, deps?: unknown[]) {
    const i = hooks.at++;
    const before = hooks.cells[i] as unknown[] | undefined;
    if (before && deps && deps.every((d, n) => Object.is(d, before[n]))) return;
    hooks.effects.push(() => {
      hooks.cells[i] = deps;
      run();
    });
  }
  const fake = { useState, useRef, useEffect, useMemo: (make: () => unknown) => make(), useCallback: (f: unknown) => f };
  return { ...real, ...fake, default: { ...real, ...fake } };
});
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => undefined, replace: () => undefined }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/ui/Toast", () => ({ useToast: () => ({ show: () => undefined }) }));
vi.mock("@/components/AccessProvider", () => ({ useAccess: () => ({ access: { email: "a@x.test" } }) }));
vi.mock("@/components/ViewModeProvider", () => ({ useViewMode: () => ({ isMobile: false }) }));
vi.mock("@/lib/useCachedResource", () => ({
  useCachedResource: (key: string) => {
    const access = key === "integrations:access";
    const data = access ? hooks.rules : hooks.rows;
    const error = access ? hooks.rulesError : null;
    return { data, loading: data === undefined && !error, error, refresh: () => undefined };
  },
}));

/** Run a component until its state settles, then run its effects. */
function render<P>(component: (props: P) => unknown, props: P): ReactElement {
  for (let pass = 0; pass < 10; pass++) {
    hooks.at = 0;
    hooks.dirty = false;
    hooks.effects = [];
    const tree = component(props) as ReactElement;
    if (hooks.dirty) continue;
    hooks.effects.forEach((run) => run());
    return tree;
  }
  throw new Error("The component did not settle.");
}

/** The props of the first element of one type, or of one tag with a ref. */
function find<P>(node: unknown, match: (el: ReactElement) => boolean): P | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const found = find<P>(child, match);
      if (found) return found;
    }
    return null;
  }
  if (!node || typeof node !== "object" || !("props" in node)) return null;
  const el = node as ReactElement<{ children?: unknown }>;
  return match(el) ? (el.props as P) : find<P>(el.props.children, match);
}

const ofType = <P,>(tree: unknown, type: unknown): P => {
  const props = find<P>(tree, (el) => el.type === type);
  if (!props) throw new Error("The element is not on screen.");
  return props;
};

const item = (over: Partial<AccessItem> = {}): AccessItem => ({
  service: "mcp-tally",
  kind: "mcp",
  mode: "everyone",
  groups: [],
  people: [],
  set_by: "",
  set_at: "",
  listed: true,
  readable: true,
  ...over,
});

/** A gateway that holds `stored`. It records the method of each request. */
function gateway(stored: AccessItem[]): string[] {
  const sent: string[] = [];
  vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    sent.push(method);
    if (method !== "PUT") return Response.json({ services: stored, readable: true });
    return Response.json(item(JSON.parse(String(init?.body)) as Partial<AccessItem>));
  });
  return sent;
}

beforeEach(() => {
  hooks.cells = [];
  hooks.rules = null;
  hooks.rulesError = null;
  hooks.rows = [];
  clearAll();
  vi.unstubAllGlobals();
});

/** The open editor. `show` draws it with the live rule, as the view does. */
function editor() {
  const saved: AccessItem[] = [];
  /** What the focus went to inside the dialog. */
  const focused: string[] = [];
  let live = item();
  // The view holds the list while the editor is open.
  put(ACCESS_KEY, { services: [live], readable: true } satisfies AccessList);
  const show = (next: AccessItem = live) => {
    live = next;
    const tree = render(WhoCanUseDialog, {
      item: live,
      label: "Tally",
      onClose: () => undefined,
      onSaved: (s: AccessItem) => void saved.push(s),
    });
    const box = find<{ ref: { current: unknown } }>(tree, (el) => el.type === "div" && "ref" in (el.props as object));
    if (box) {
      box.ref.current = {
        querySelector: (selector: string) => ({ focus: () => void focused.push(selector) }),
        querySelectorAll: () => [],
      };
    }
    return ofType<WhoCanUseBodyProps>(tree, WhoCanUseBody);
  };
  /** Press Save, draw the save in flight, and wait for it to end. */
  const press = async () => {
    const write = (show().onSave as () => unknown)();
    expect(show().saving).toBe(true);
    await write;
    return show();
  };
  return { show, press, saved, focused };
}

describe("a save from the editor", () => {
  it("reads the stored rule before it writes", async () => {
    const sent = gateway([item()]);
    const e = editor();
    e.show().onMode("admins");
    await e.press();
    expect(sent).toEqual(["GET", "PUT"]);
    expect(e.saved.map((s) => s.mode)).toEqual(["admins"]);
    expect(e.focused).toEqual([]);
  });

  it("writes nothing over a rule that the admin did not see, and focuses the reload", async () => {
    const now = item({ mode: "chosen", people: ["b@x.test"] });
    const sent = gateway([now]);
    const e = editor();
    e.show().onMode("admins");
    const body = await e.press();
    expect(sent).toEqual(["GET"]);
    expect(e.saved).toEqual([]);
    expect(body.changed).toEqual(now);
    expect(body.mode).toBe("admins");
    expect(body.saving).toBe(false);
    expect(e.focused).toEqual(["[data-notice]"]);
  });

  it("focuses the words of a rule that is gone", async () => {
    const sent = gateway([]);
    const e = editor();
    e.show().onMode("admins");
    const body = await e.press();
    expect(sent).toEqual(["GET"]);
    expect(body.error).toBe(RULE_GONE);
    expect(e.focused).toEqual(["[data-notice]"]);
  });

  it("compares with the rule that the choices started from, not the newest one", async () => {
    const now = item({ mode: "chosen", people: ["b@x.test"] });
    const sent = gateway([now]);
    const e = editor();
    e.show().onMode("admins");
    e.show(now);
    // Save is off on screen. A press that gets through still writes nothing.
    await e.press();
    expect(sent).toEqual(["GET"]);
    expect(e.saved).toEqual([]);
  });
});

describe("a newer rule under the open editor", () => {
  const now = item({ mode: "chosen", people: ["b@x.test"] });

  it("is followed while the admin made no choice", () => {
    const e = editor();
    e.show();
    const body = e.show(now);
    expect(body.mode).toBe("chosen");
    expect(body.item).toEqual(now);
    expect(body.changed).toBeNull();
  });

  it("stops the save after a choice, until the reload", () => {
    const e = editor();
    e.show().onMode("admins");
    const body = e.show(now);
    expect(body.changed).toEqual(now);
    expect(body.mode).toBe("admins");
    body.onReload();
    expect(e.show().changed).toBeNull();
    expect(e.show().mode).toBe("chosen");
  });
});

describe("Set to Everyone from the list", () => {
  const old = item({ service: "mcp-old", kind: "", mode: "admins", listed: false });

  type Confirm = { onConfirm: () => unknown; children: ReactElement<{ children: string }> | null };

  /** Open the confirm of the stored rule, then confirm it. */
  async function confirm() {
    const list: AccessList = { services: [old], readable: true };
    hooks.rules = list;
    put(ACCESS_KEY, list);
    const show = () => render(ConnectionsView, { canManage: true });
    const footer = ofType<{ footer: ReactElement }>(show(), ConnectionList).footer;
    ofType<{ onClear: (i: AccessItem) => void }>(footer, UnmatchedRules).onClear(old);
    await ofType<Confirm>(show(), ConfirmDialog).onConfirm();
    return show();
  }

  it("reads the stored rule, then writes", async () => {
    const sent = gateway([old]);
    const tree = await confirm();
    expect(sent).toEqual(["GET", "PUT"]);
    expect(find(tree, (el) => el.type === ConfirmDialog)).toBeNull();
  });

  it("clears nothing when the rule changed after the confirm opened", async () => {
    const sent = gateway([{ ...old, mode: "chosen", people: ["b@x.test"] }]);
    const tree = await confirm();
    expect(sent).toEqual(["GET"]);
    expect(ofType<Confirm>(tree, ConfirmDialog).children?.props.children).toBe(CLEAR_CHANGED);
  });
});

describe("a cold load of the list", () => {
  type List = { loading: boolean; rowExtra: (r: Connection) => unknown };
  const mcp = row({ id: "mcp:tally", kind: "mcp", provider: "mcp", detail: { name: "tally" } });
  const list = () => ofType<List>(render(ConnectionsView, { canManage: true }), ConnectionList);

  beforeEach(() => {
    vi.useFakeTimers();
    hooks.rows = [mcp];
    hooks.rules = undefined;
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("draws each row and its control together", () => {
    expect(list().loading).toBe(true);
    hooks.rules = { services: [item()], readable: true } satisfies AccessList;
    expect(list().loading).toBe(false);
    expect(list().rowExtra(mcp)).not.toBeNull();
  });

  it("ends the wait at once off the flag or with no right (404 and 403)", () => {
    expect(list().loading).toBe(true);
    hooks.rules = null;
    expect(list().loading).toBe(false);
  });

  it("ends the wait at once when the rules read fails", () => {
    expect(list().loading).toBe(true);
    hooks.rulesError = "Gateway unreachable.";
    expect(list().loading).toBe(false);
  });

  it("does not wait when no row can take a rule", () => {
    hooks.rows = [row({ id: "zoho:1" })];
    expect(list().loading).toBe(false);
  });

  it("shows the list after the bound while the rules read hangs", () => {
    expect(list().loading).toBe(true);
    vi.advanceTimersByTime(ACCESS_WAIT_MS - 1);
    expect(list().loading).toBe(true);
    vi.advanceTimersByTime(1);
    expect(list().loading).toBe(false);
  });

  it("starts the bound when the list arrives", () => {
    hooks.rows = undefined;
    expect(list().loading).toBe(true);
    vi.advanceTimersByTime(ACCESS_WAIT_MS * 3);
    hooks.rows = [mcp];
    expect(list().loading).toBe(true);
    vi.advanceTimersByTime(ACCESS_WAIT_MS);
    expect(list().loading).toBe(false);
  });
});

describe("the one door of the write", () => {
  const SRC = join(__dirname, "../../..");
  const sources = (readdirSync(SRC, { recursive: true }) as string[])
    .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.tsx?$/.test(f))
    .map((f) => ({ path: relative(SRC, join(SRC, f)).replaceAll("\\", "/"), text: readFileSync(join(SRC, f), "utf-8") }));
  const holding = (needle: string) => sources.filter((s) => s.text.includes(needle)).map((s) => s.path).sort();

  it("lets only lib/accessSave.ts call setIntegrationAccess", () => {
    // `accessSave` reads the stored rule first. A component that calls the PUT
    // itself replaces a rule that the admin did not see.
    expect(holding("setIntegrationAccess")).toEqual([
      "app/integrations/lib/accessSave.ts",
      "app/integrations/lib/api.ts",
    ]);
  });

  it("lets only lib/api.ts name the route of the write", () => {
    expect(holding("/api/admin/integration-access")).toEqual(["app/integrations/lib/api.ts"]);
  });
});
