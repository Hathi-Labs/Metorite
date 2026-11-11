import { describe, expect, it } from "vitest";

import { PANES, type NavPane } from "@/lib/nav";

import {
  DAY_MS,
  SUGGEST_HIDE_DAYS,
  SUGGEST_MIN_OPENS,
  SUGGEST_WINDOW_DAYS,
  paneHrefOf,
  suggestPin,
  suggestionWhy,
} from "./pinSuggest";
import { OPENS_CAP, hideSuggestion, hiddenKey, opensKey, readHidden, readOpens, recordOpen, type StoreLike } from "./pinSuggestStore";
import { STARRABLE } from "./presets";

const held: NavPane[] = PANES.filter((p) => STARRABLE.has(p.href)).slice(0, 4);
const [A, B, C] = held;
const NOW = 1_800_000_000_000;
const times = (n: number, at = NOW - DAY_MS) => Array.from({ length: n }, (_, i) => at - i * 1000);
const run = (over: Partial<Parameters<typeof suggestPin>[0]> = {}) =>
  suggestPin({ opens: {}, starred: [], hidden: {}, panes: held, now: NOW, ...over });

function memory(): StoreLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
}

describe("the constants", () => {
  it("hold the numbers of the spec", () => {
    expect([SUGGEST_MIN_OPENS, SUGGEST_WINDOW_DAYS, SUGGEST_HIDE_DAYS]).toEqual([5, 7, 30]);
    expect(held.length).toBeGreaterThanOrEqual(3);
  });
});

describe("suggestPin", () => {
  it("suggests a pane opened 5 times in 7 days", () => {
    const s = run({ opens: { [A.href]: times(5) } });
    expect(s).toEqual({ href: A.href, opens: 5, why: `You opened ${A.label} 5 times this week. Pin it?` });
  });
  it("does not suggest at 4 opens", () => {
    expect(run({ opens: { [A.href]: times(4) } })).toBeNull();
  });
  it("does not count opens older than 7 days", () => {
    expect(run({ opens: { [A.href]: times(5, NOW - 8 * DAY_MS) } })).toBeNull();
    expect(run({ opens: { [A.href]: [...times(4), NOW - 8 * DAY_MS] } })).toBeNull();
  });
  it("does not suggest a starred pane", () => {
    expect(run({ opens: { [A.href]: times(9) }, starred: [A.href] })).toBeNull();
  });
  it("hides after a No for 30 days and suggests again at the 30-day mark", () => {
    const hiddenAt = NOW;
    const hidden = { [A.href]: hiddenAt + SUGGEST_HIDE_DAYS * DAY_MS };
    const at = (d: number) => hiddenAt + d * DAY_MS;
    const opensAt = (d: number) => ({ [A.href]: times(6, at(d)) });
    expect(run({ opens: opensAt(29), hidden, now: at(29) })).toBeNull();
    expect(run({ opens: opensAt(30), hidden, now: at(30) })?.href).toBe(A.href);
    expect(run({ opens: opensAt(40), hidden, now: at(40) })?.href).toBe(A.href);
  });
  it("never suggests a pane the member does not hold", () => {
    expect(run({ opens: { [A.href]: times(9) }, panes: [B, C] })).toBeNull();
  });
  it("never suggests an install href or an unknown href", () => {
    const fake = { ...A, href: "/apps/field-visits", label: "Field visits" };
    expect(run({ opens: { "/apps/field-visits": times(9) }, panes: [fake] })).toBeNull();
    expect(paneHrefOf("/apps/field-visits")).toBeNull();
  });
  it("takes the highest count, and ties follow the pane order", () => {
    expect(run({ opens: { [A.href]: times(5), [B.href]: times(8) } })?.href).toBe(B.href);
    expect(run({ opens: { [A.href]: times(6), [B.href]: times(6) } })?.href).toBe(A.href);
    expect(run({ opens: { [A.href]: times(6), [B.href]: times(6) }, panes: [B, A] })?.href).toBe(B.href);
  });
  it("returns at most one suggestion", () => {
    const s = run({ opens: { [A.href]: times(6), [B.href]: times(7), [C.href]: times(9) } });
    expect(Array.isArray(s)).toBe(false);
    expect(s?.href).toBe(C.href);
  });
  it("writes the why sentence", () => {
    expect(suggestionWhy("Field visits", 9)).toBe("You opened Field visits 9 times this week. Pin it?");
  });
});

describe("paneHrefOf", () => {
  it("maps a route to its pane", () => {
    const tasks = PANES.find((p) => p.href === "/tasks");
    if (tasks && STARRABLE.has("/tasks")) {
      expect(paneHrefOf("/tasks/123")).toBe("/tasks");
      expect(paneHrefOf("/tasks?x=1")).toBe("/tasks");
    }
    expect(paneHrefOf(`${A.href}/deep/path`)).toBe(A.href);
    expect(paneHrefOf("/")).toBeNull();
    expect(paneHrefOf(null)).toBeNull();
    expect(paneHrefOf("/nowhere")).toBeNull();
  });
});

describe("the store", () => {
  it("records an open and reads it back", () => {
    const s = memory();
    recordOpen("a@x.com", `${A.href}/1`, NOW, s);
    expect(readOpens("a@x.com", s)).toEqual({ [A.href]: [NOW] });
  });
  it("is per account, and the email is lowercased", () => {
    const s = memory();
    recordOpen("A@x.com", A.href, NOW, s);
    expect(readOpens("a@x.com", s)[A.href]).toEqual([NOW]);
    expect(readOpens("b@x.com", s)).toEqual({});
    hideSuggestion("a@x.com", A.href, NOW, s);
    expect(readHidden("b@x.com", NOW, s)).toEqual({});
    expect(opensKey("A@X.com")).toBe(opensKey("a@x.com"));
  });
  it("trims times older than 7 days", () => {
    const s = memory();
    recordOpen("a@x.com", A.href, NOW - 8 * DAY_MS, s);
    recordOpen("a@x.com", B.href, NOW - 2 * DAY_MS, s);
    recordOpen("a@x.com", A.href, NOW, s);
    expect(readOpens("a@x.com", s)).toEqual({ [A.href]: [NOW], [B.href]: [NOW - 2 * DAY_MS] });
  });
  it("caps the total and drops the oldest", () => {
    const s = memory();
    for (let i = 0; i < OPENS_CAP + 20; i++) recordOpen("a@x.com", i % 2 ? A.href : B.href, NOW - 1_000_000 + i, s);
    const all = Object.values(readOpens("a@x.com", s)).flat();
    expect(all).toHaveLength(OPENS_CAP);
    expect(Math.min(...all)).toBe(NOW - 1_000_000 + 20);
  });
  it("ignores an install route and a route outside the panes", () => {
    const s = memory();
    recordOpen("a@x.com", "/apps/field-visits", NOW, s);
    recordOpen("a@x.com", "/nowhere", NOW, s);
    expect(s.data.size).toBe(0);
  });
  it("hides for 30 days and drops an expired hide on read", () => {
    const s = memory();
    hideSuggestion("a@x.com", A.href, NOW, s);
    const until = NOW + SUGGEST_HIDE_DAYS * DAY_MS;
    expect(readHidden("a@x.com", NOW + 29 * DAY_MS, s)).toEqual({ [A.href]: until });
    expect(readHidden("a@x.com", until, s)).toEqual({});
  });
  it("reads a corrupted value as empty", () => {
    const s = memory();
    s.data.set(opensKey("a@x.com"), "{not json");
    s.data.set(hiddenKey("a@x.com"), "[1,2]");
    expect(readOpens("a@x.com", s)).toEqual({});
    expect(readHidden("a@x.com", NOW, s)).toEqual({});
    s.data.set(opensKey("a@x.com"), JSON.stringify({ [A.href]: ["x", 5, null], [B.href]: "no" }));
    expect(readOpens("a@x.com", s)).toEqual({ [A.href]: [5] });
  });
  it("swallows a storage that throws", () => {
    const bad: StoreLike = {
      getItem: () => {
        throw new Error("off");
      },
      setItem: () => {
        throw new Error("full");
      },
    };
    expect(() => recordOpen("a@x.com", A.href, NOW, bad)).not.toThrow();
    expect(() => hideSuggestion("a@x.com", A.href, NOW, bad)).not.toThrow();
    expect(readOpens("a@x.com", bad)).toEqual({});
    expect(readHidden("a@x.com", NOW, bad)).toEqual({});
  });
});
