/**
 * STU-D11b fence: page state lives in the URL.
 *
 * A fake history stands behind a mocked `next/navigation`. The hook renders with
 * `renderToStaticMarkup`, which runs the hook and nothing else. The setters it returns write to
 * the fake history, as the router would. A "reload" is a new render of the same URL.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hist = vi.hoisted(() => {
  const h = {
    entries: ["/studio/page-preview"],
    at: 0,
    pushes: 0,
    replaces: 0,
    reset(url = "/studio/page-preview") {
      h.entries = [url];
      h.at = 0;
      h.pushes = 0;
      h.replaces = 0;
    },
    get url() { return h.entries[h.at]; },
    back() { if (h.at > 0) h.at -= 1; },
    forward() { if (h.at < h.entries.length - 1) h.at += 1; },
  };
  return h;
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: (href: string) => {
      hist.entries = [...hist.entries.slice(0, hist.at + 1), href];
      hist.at += 1;
      hist.pushes += 1;
    },
    replace: (href: string) => {
      hist.entries[hist.at] = href;
      hist.replaces += 1;
    },
  }),
  usePathname: () => hist.url.split("?")[0],
  useSearchParams: () => new URLSearchParams(hist.url.split("?")[1] ?? ""),
}));

import { FILTER_DEBOUNCE_MS, mergePageQuery, usePageState, type PageStateApi } from "@/lib/studio/usePageState";
import { PAGE_STATE_LIMITS, type PageTabs } from "@/lib/studio/pageState";

const PAGE: PageTabs = { tabs: [{ id: "list" }, { id: "plan" }] };

/** One hook instance on the current URL: a mount, or a reload. */
function mount(page: PageTabs | undefined = PAGE): PageStateApi {
  let api: PageStateApi | undefined;
  function Probe() {
    api = usePageState(page);
    return null;
  }
  renderToStaticMarkup(createElement(Probe));
  return api as PageStateApi;
}

beforeEach(() => {
  hist.reset();
  vi.useFakeTimers();
});
afterEach(() => vi.useRealTimers());

describe("the round trip through the URL", () => {
  it("keeps a tab, a filter and a pick over a reload", () => {
    const a = mount();
    a.setTab("plan");
    mount().setSelected("v2");
    mount().setFilter("q", "press");
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    const before = mount().state;
    expect(before.tab).toBe("plan");
    expect(before.selected).toBe("v2");
    expect(before.filters.q).toBe("press");
    // A reload: a new instance on the same URL.
    const after = mount().state;
    expect(after).toEqual(before);
    expect(hist.url).toContain("tab=plan");
    expect(hist.url).toContain("sel=v2");
    expect(hist.url).toContain("f.q=press");
  });

  it("holds the state only in the URL", () => {
    mount().setTab("plan");
    hist.reset("/studio/page-preview");
    expect(mount().state.tab).toBeNull();
  });

  it("restores the earlier tab on Back, and the later one on Forward", () => {
    mount().setTab("plan");
    mount().setTab("list");
    expect(mount().state.tab).toBe("list");
    hist.back();
    expect(mount().state.tab).toBe("plan");
    hist.back();
    expect(mount().state.tab).toBeNull();
    hist.forward();
    expect(mount().state.tab).toBe("plan");
  });

  it("closes the side view on Back after a pick", () => {
    mount().setSelected("v1");
    expect(mount().state.selected).toBe("v1");
    hist.back();
    expect(mount().state.selected).toBeNull();
  });

  it("clears the pick with null", () => {
    mount().setSelected("v1");
    mount().setSelected(null);
    expect(mount().state.selected).toBeNull();
    expect(hist.url).not.toContain("sel=");
  });
});

describe("a filter edit", () => {
  it("adds no history entry, and replaces the current one", () => {
    mount().setTab("plan");
    const entries = hist.entries.length;
    const pushes = hist.pushes;
    const f = mount();
    f.setFilter("q", "p");
    f.setFilter("q", "pr");
    f.setFilter("q", "pre");
    expect(hist.replaces).toBe(0);
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS - 1);
    expect(hist.replaces).toBe(0);
    vi.advanceTimersByTime(1);
    expect(hist.replaces).toBe(1);
    expect(hist.pushes).toBe(pushes);
    expect(hist.entries).toHaveLength(entries);
    expect(mount().state.filters.q).toBe("pre");
  });

  it("clears with an empty value", () => {
    mount().setFilter("q", "x");
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    mount().setFilter("q", "");
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    expect(hist.url).not.toContain("f.q");
  });

  it("is kept when a tab change follows inside the wait", () => {
    const a = mount();
    a.setFilter("q", "press");
    a.setTab("plan");
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS * 2);
    const s = mount().state;
    expect(s.tab).toBe("plan");
    expect(s.filters.q).toBe("press");
  });
});

describe("keys that the hook does not own", () => {
  it("stay in the URL after each write", () => {
    hist.reset("/studio/page-preview?utm_source=mail&task=abc");
    mount().setTab("plan");
    mount().setSelected("v1");
    mount().setFilter("q", "x");
    vi.advanceTimersByTime(FILTER_DEBOUNCE_MS);
    const p = new URLSearchParams(hist.url.split("?")[1]);
    expect(p.get("utm_source")).toBe("mail");
    expect(p.get("task")).toBe("abc");
    expect(p.get("tab")).toBe("plan");
  });

  it("mergePageQuery drops an old owned key and keeps a foreign one", () => {
    const q = mergePageQuery("task=1&tab=list&sel=a&f.q=x&utm_x=2", { selected: null, filters: {}, tab: "plan" }, PAGE);
    expect(new URLSearchParams(q).get("tab")).toBe("plan");
    expect(q).not.toContain("sel=");
    expect(q).not.toContain("f.q");
    expect(q).toContain("task=1");
    expect(q).toContain("utm_x=2");
  });
});

describe("a hostile URL", () => {
  it("gives a safe state", () => {
    const filters = Array.from({ length: 10000 }, (_, i) => `f.n${i}=v`).join("&");
    hist.reset(`/studio/page-preview?tab=nope&sel=${"x".repeat(5000)}&f.__proto__=1&f.constructor=2&${filters}`);
    const s = mount().state;
    expect(s.tab).toBeNull();
    expect(s.selected).toBeNull();
    expect(Object.keys(s.filters).length).toBeLessThanOrEqual(PAGE_STATE_LIMITS.filters);
    expect(Object.getPrototypeOf(s.filters)).toBeNull();
    expect(s.filters.constructor).toBeUndefined();
  });

  it("cuts a long filter value and a control character", () => {
    hist.reset(`/studio/page-preview?f.q=${"a".repeat(1_000_000)}&f.r=a%00b&sel=a%07b`);
    const s = mount().state;
    expect(s.filters.q).toBeUndefined();
    expect(s.filters.r).toBe("ab");
    expect(s.selected).toBeNull();
  });

  it("never throws on a broken query", () => {
    hist.reset("/studio/page-preview?tab=%E0%A4%A&sel=%&f.=1&&=&f.q");
    expect(() => mount().state).not.toThrow();
  });

  it("binds a tab that the page does not declare, on read", () => {
    hist.reset("/studio/page-preview?tab=plan");
    expect(mount(PAGE).state.tab).toBe("plan");
    expect(mount({ tabs: [{ id: "list" }] }).state.tab).toBeNull();
  });

  it("binds a write as well as a read", () => {
    mount().setSelected("y".repeat(500));
    expect(hist.url).not.toContain("sel=");
  });
});

describe("an edit typed in the last 300 ms", () => {
  it("is written when the page unmounts on its own path, and not when the member left it", async () => {
    const { shouldFlushOnUnmount } = await import("@/lib/studio/usePageState");
    expect(shouldFlushOnUnmount(true, "/studio/page-preview", "/studio/page-preview")).toBe(true);
    expect(shouldFlushOnUnmount(false, "/studio/page-preview", "/studio/page-preview")).toBe(false);
    expect(shouldFlushOnUnmount(true, "/studio", "/studio/page-preview")).toBe(false);
  });

  it("is flushed by the unmount cleanup, not dropped", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("./usePageState.ts", import.meta.url), "utf-8");
    const cleanup = src.slice(src.indexOf("useEffect(() => () =>"));
    expect(cleanup).toMatch(/shouldFlushOnUnmount\([\s\S]*write\(\(s\) => s, "replace"\)/);
  });
});
