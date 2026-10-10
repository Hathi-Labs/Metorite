/**
 * The shell's one bell (NS-6 slice 6a, `navigation_shell.md` §7.2).
 *
 * Five claims, each one a way the slice breaks:
 *
 *   1. The badge prints the feed's `count`, capped at "99+", and nothing at 0.
 *      The spoken name says "Needs you, N items".
 *   2. The live bell takes its number from the feed's `count`, not from the
 *      rows it holds. A feed of 42 with two rows on hand says 42.
 *   3. The flag decides it. Off, the shell bar draws no bell, and Projects and
 *      My Tasks still mount `NotificationBell`. On, the bar draws the bell,
 *      and neither app mounts its own.
 *   4. The bell and My Day's card draw ONE list, `NeedsList`. The bell holds
 *      no row, group or act of its own.
 *   5. The flag reads its literal, so Next can inline it.
 *
 * The runner is `environment: "node"`, so the controls render to strings.
 * The browser half is `e2e/shell-bell.spec.ts`.
 *
 * Mutations, run by hand (2026-10-10):
 *   - drop `dockOn ?` in `ShellFrame`: "flag off: the shell bar draws no bell" fails;
 *   - drop `shellDockOn() ?` at a NotificationBell mount: the app test fails;
 *   - take the badge from `items.length`: "takes its number from the feed's count" fails.
 */
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { NO_ACCESS } from "@/lib/access";
import { clearAll, put } from "@/lib/dataCache";

const access = { features: ["tasks", "projects", "email"] as string[] };

vi.mock("@/components/AccessProvider", () => ({
  useAccess: () => ({
    access: { ...NO_ACCESS, authenticated: true, features: access.features },
    loading: false,
    stale: false,
    refresh: async () => {},
  }),
}));

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { name: "Asha Rao", email: "asha@example.com" } } }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/tasks",
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(),
}));

import { needsKey, type NeedsItem } from "./needs";
import { BellButton, ShellBell, bellBadge, bellLabel } from "./ShellBell";
import { shellDockOn } from "./dockFlag";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
/** Source with comments removed, so prose about a mount is not a mount. */
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/\\])\/\/[^\n]*/g, "");

const row = (id: string): NeedsItem => ({
  id: `tasks:${id}`,
  app: "tasks",
  kind: "overdue",
  title: `Task ${id}`,
  detail: null,
  href: `/projects?task=${id}`,
  at: null,
  act: "done",
  act_ref: id,
});

afterEach(() => {
  vi.unstubAllEnvs();
  access.features = ["tasks", "projects", "email"];
  clearAll();
});

describe("the badge and the spoken name", () => {
  it("prints the count, caps it at 99+, and prints nothing for zero", () => {
    expect(bellBadge(undefined)).toBeNull();
    expect(bellBadge(0)).toBeNull();
    expect(bellBadge(-2)).toBeNull();
    expect(bellBadge(Number.NaN)).toBeNull();
    expect(bellBadge(1)).toBe("1");
    expect(bellBadge(99)).toBe("99");
    expect(bellBadge(100)).toBe("99+");
    expect(bellBadge(150)).toBe("99+");
  });

  it("says what it is and the real number", () => {
    expect(bellLabel(undefined)).toBe("Needs you");
    expect(bellLabel(0)).toBe("Needs you, no items");
    expect(bellLabel(1)).toBe("Needs you, 1 item");
    expect(bellLabel(3)).toBe("Needs you, 3 items");
    // The badge caps. The name does not.
    expect(bellLabel(150)).toBe("Needs you, 150 items");
  });

  it("draws the badge on the Bell glyph, in the tone that means 'waits for you'", () => {
    const html = renderToStaticMarkup(createElement(BellButton, { count: 3, open: false, onOpen: () => {} }));
    expect(html).toContain('aria-label="Needs you, 3 items"');
    expect(html).toContain('aria-haspopup="dialog"');
    expect(html).toContain('data-shell-bell="count"');
    expect(html).toMatch(/data-nav-badge="warning"[^>]*>3<\/span>/);
    // The one primitive, so the control carries the house state layer.
    expect(html).toContain("cc-control");
  });

  it("is quiet at zero and before the feed answers", () => {
    for (const count of [0, undefined]) {
      const html = renderToStaticMarkup(createElement(BellButton, { count, open: false, onOpen: () => {} }));
      expect(html).toContain('data-shell-bell="quiet"');
      expect(html).not.toContain("data-nav-badge");
    }
  });

  it("prints 99+ past the cap", () => {
    const html = renderToStaticMarkup(createElement(BellButton, { count: 150, open: false, onOpen: () => {} }));
    expect(html).toMatch(/>99\+<\/span>/);
    expect(html).toContain('aria-label="Needs you, 150 items"');
  });
});

describe("the live bell reads the feed", () => {
  it("takes its number from the feed's count, not from the rows on hand", () => {
    put(needsKey(), { count: 42, items: [row("a"), row("b")], sources: { tasks: "ok" } });
    const html = renderToStaticMarkup(createElement(ShellBell));
    expect(html).toContain('aria-label="Needs you, 42 items"');
    expect(html).toMatch(/>42<\/span>/);
  });

  it("reads My Day's own cache key, so the two stay in step", () => {
    const src = code(read("./NeedsYouCard.tsx"));
    expect(src).toMatch(/useCachedResource<NeedsFeed>\(enabled \? needsKey\(\) : null/);
    expect(code(read("./ShellBell.tsx"))).toMatch(/useNeedsYou\(allowed\)/);
  });

  it("draws nothing for a member who holds no source of the feed", () => {
    access.features = ["tasks", "chat"];
    expect(renderToStaticMarkup(createElement(ShellBell))).toBe("");
  });
});

describe("the flag decides where the bell is", () => {
  // `ShellFrame` pulls the command bar's whole graph. Pay its first
  // transform once, outside the five-second budget of each test.
  beforeAll(async () => {
    await import("./ShellBar");
  }, 60_000);

  async function frame(): Promise<string> {
    const { ShellFrame } = await import("./ShellBar");
    return renderToStaticMarkup(createElement(ShellFrame, null, createElement("main")));
  }

  it("flag off: the shell bar draws no bell", async () => {
    vi.stubEnv("NEXT_PUBLIC_SHELL_DOCK", "");
    const html = await frame();
    expect(html).toContain("data-shell-bar");
    expect(html).not.toContain("data-shell-bell");
  });

  it("flag on: the shell bar draws the bell, before the activity control", async () => {
    vi.stubEnv("NEXT_PUBLIC_SHELL_DOCK", "1");
    const html = await frame();
    const bar = html.slice(html.indexOf("<header"), html.indexOf("</header>"));
    expect(bar).toContain('data-shell-bell="quiet"');
    expect(bar).toContain('aria-label="Needs you"');
  });

  it("the flag reads its literal, so a production build inlines it", () => {
    vi.stubEnv("NEXT_PUBLIC_SHELL_DOCK", "1");
    expect(shellDockOn()).toBe(true);
    vi.stubEnv("NEXT_PUBLIC_SHELL_DOCK", "");
    expect(shellDockOn()).toBe(false);
    const src = code(read("./dockFlag.ts"));
    expect(src).toContain('process.env.NEXT_PUBLIC_SHELL_DOCK === "1"');
    expect(src).toContain('localStorage.getItem("cc-shell-dock") === "1"');
    expect(src).toContain('process.env.NODE_ENV !== "production"');
  });

  it("each app mounts NotificationBell only with the flag off", () => {
    // Projects and My Tasks, each at its phone bar and its desktop bar. With
    // the flag on the shell's bell replaces them, and NS-9 deletes them.
    for (const [rel, mounts] of [
      ["../../app/projects/page.tsx", 2],
      ["../../app/tasks/page.tsx", 2],
    ] as const) {
      const src = code(read(rel));
      const all = src.match(/<NotificationBell\b/g) ?? [];
      const gated = src.match(/shellDockOn\(\) \? (?:null|undefined) : <NotificationBell\b/g) ?? [];
      expect(all.length, rel).toBe(mounts);
      expect(gated.length, `${rel}: a NotificationBell mount outside the flag`).toBe(mounts);
    }
  });

  it("the sidebar head and the phone drawer draw the bell only with the flag on", () => {
    expect(code(read("../../components/Sidebar.tsx"))).toMatch(/\{!barOn && dockOn \? <ShellBell \/> : null\}/);
    const shell = code(read("../../components/AppShell.tsx"));
    expect(shell).toMatch(/\{dockOn \? <ShellBell onBeforeOpen=\{close\}/);
    expect(shell.match(/\{dockOn \? <ShellBellHost placement=/g)?.length).toBe(2);
  });
});

describe("one list for the bell and the card", () => {
  it("the card and the bell's panel both render NeedsList", () => {
    const card = code(read("./NeedsYouCard.tsx"));
    expect(card).toMatch(/export function NeedsList\(/);
    expect(card).toMatch(/<HomeCard[^>]*>\s*<NeedsList needs=\{needs\} now=\{now\} \/>/);
    const bell = code(read("./ShellBell.tsx"));
    expect(bell).toMatch(/^import \{[^}]*\bNeedsList\b[^}]*\} from "\.\/NeedsYouCard";/m);
    expect(bell).toMatch(/<NeedsList needs=\{needs\} now=\{now\} \/>/);
  });

  it("the bell holds no row, group or act of its own", () => {
    const bell = code(read("./ShellBell.tsx"));
    // A second row, a second grouping or a second act path is a copy that drifts.
    expect(bell).not.toMatch(/data-row-act|groupNeeds|shownNeeds|runAct|KIND_LABELS|markDoneFromHome|notificationsApi/);
    expect(bell).not.toMatch(/<li\b|<ul\b/);
  });

  it("the rows an act took off live in one store, shared by every reader", () => {
    const card = code(read("./NeedsYouCard.tsx"));
    expect(card).toMatch(/useSyncExternalStore\(subscribeActs, \(\) => removedIds/);
    expect(card).toMatch(/rowMover\(item\.id, removeRow, setRowError, from\)/);
    // No reader keeps a private copy any more.
    expect(card).not.toMatch(/useState<ReadonlySet<string>>/);
  });

  it("a row that leaves the panel hands focus on, as it does in the card", () => {
    expect(code(read("./HomeCard.tsx"))).toMatch(/closest\("\[data-home-card\], \[data-needs-panel\]"\)/);
    const bell = code(read("./ShellBell.tsx"));
    expect(bell).toContain('data-needs-panel=""');
    expect(bell).toContain('data-leave-focus=""');
  });

  it("the bell's host says the store's Undo on a page that holds none", () => {
    expect(code(read("./ShellBell.tsx"))).toMatch(/<UndoToastFallback \/>/);
    const undo = code(read("../../app/tasks/components/UndoToast.tsx"));
    // The fallback binds no key, and stands down while a page holds the toast.
    expect(undo).toMatch(/useUndoToastSync\(false, !held, false\)/);
  });
});
