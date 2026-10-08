import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { PANES, type NavPane } from "@/lib/nav";
import {
  JOBS,
  buildItems,
  contextPane,
  rank,
  readRecent,
  rememberRecent,
  score,
  type BarItem,
} from "./registry";

/**
 * NS-1, the command bar's tier 0 (`navigation_shell.md` §6.2, §6.4, §6.7).
 * Each case is written so the obvious wrong build fails it.
 */

const pane = (href: string, label: string, note = ""): NavPane => ({
  href,
  label,
  note,
  icon: "Box",
  launch: "live",
});

const HELD = [
  pane("/tasks", "My Tasks", "Your tasks"),
  pane("/email", "Email", "AI-powered inbox"),
  pane("/projects", "Projects", "Departments, projects and team tasks"),
  pane("/people", "People", "Directory, skills and org chart"),
  pane("/people/me", "My Profile", "Your skills, CV, working hours"),
];

describe("the jobs", () => {
  it("each job belongs to a real pane, and opens a path of that pane", () => {
    const hrefs = new Set(PANES.map((p) => p.href));
    for (const j of JOBS) {
      expect(hrefs.has(j.app), `${j.id} names a pane that is gone`).toBe(true);
      expect(j.href.startsWith(j.app), `${j.id} opens outside its app`).toBe(true);
    }
  });

  it("job ids are unique", () => {
    expect(new Set(JOBS.map((j) => j.id)).size).toBe(JOBS.length);
  });
});

describe("buildItems", () => {
  it("offers a job only when the member holds its app", () => {
    const keys = buildItems(HELD).map((i) => i.key);
    expect(keys).toContain("do:capture");
    expect(keys).toContain("do:compose");
    // Calendar is not held here, so "Plan my day" is not offered.
    expect(keys).not.toContain("do:plan-day");
  });

  it("offers every held app, and no app the member does not hold", () => {
    const go = buildItems(HELD).filter((i) => i.group === "go");
    expect(go.map((i) => i.href)).toEqual(HELD.map((p) => p.href));
  });

  it("says what each item does, in plain words (§6.7 rule 5)", () => {
    for (const i of buildItems(HELD)) {
      expect(i.label).not.toMatch(/\/|\?|=/);
      expect(i.hint).not.toMatch(/^\//);
    }
    expect(buildItems(HELD).find((i) => i.key === "do:compose")!.hint).toBe("Start · Email");
    expect(buildItems(HELD).find((i) => i.key === "go:/email")!.label).toBe("Open Email");
  });
});

describe("contextPane", () => {
  it("names the app the member is in, by the longest match", () => {
    expect(contextPane("/people/me", HELD)?.href).toBe("/people/me");
    expect(contextPane("/people/abc", HELD)?.href).toBe("/people");
    expect(contextPane("/email", HELD)?.href).toBe("/email");
  });

  it("never matches a sibling that only shares a prefix", () => {
    expect(contextPane("/emailsettings", HELD)).toBeNull();
    expect(contextPane("/", HELD)).toBeNull();
  });
});

describe("score and rank", () => {
  const items = buildItems(HELD);
  const top = (query: string, context: string | null = null, recent: string[] = []) =>
    rank({ items, query, context, recent })[0]?.key;

  it("finds a job by the words a member would type, not only its label", () => {
    expect(top("new email")).toBe("do:compose");
    expect(top("compose")).toBe("do:compose");
    expect(top("remind me")).toBe("do:capture");
    expect(top("who")).toBe("do:find-person");
    expect(top("create a task")).toBe("do:capture");
    expect(top("open projects")).toBe("go:/projects");
    expect(top("go to my profile")).toBe("go:/people/me");
  });

  it("a verb alone fits its kind, never the other kind", () => {
    const keys = rank({ items, query: "open", context: null, recent: [] }).map((i) => i.key);
    expect(keys.length).toBeGreaterThan(0);
    expect(keys.every((k) => k.startsWith("go:"))).toBe(true);
  });

  it("reads a sentence: a name it does not know does not hide the job", () => {
    expect(top("new email to priya")).toBe("do:compose");
    expect(top("write a message to the team")).toBe("do:compose");
  });

  it("finds nothing when most words miss, or only a verb lands", () => {
    expect(rank({ items, query: "email holiday party budget", context: null, recent: [] })).toEqual([]);
    expect(rank({ items, query: "new holiday", context: null, recent: [] })).toEqual([]);
  });

  it("ranks a full match above a partial one", () => {
    const full = score(items.find((i) => i.key === "do:compose")!, "write email");
    const part = score(items.find((i) => i.key === "do:compose")!, "write email priya");
    expect(full).toBeGreaterThan(part);
  });

  it("puts a job above its app at an equal match (§6.2: Do first)", () => {
    const ranked = rank({ items, query: "email", context: null, recent: [] }).map((i) => i.key);
    expect(ranked.indexOf("do:compose")).toBeLessThan(ranked.indexOf("go:/email"));
  });

  it("ranks the app the member is in first (§6.4 rule 3)", () => {
    const tasks: BarItem = { ...items.find((i) => i.key === "go:/tasks")!, label: "Open tasks", words: ["tasks"] };
    const proj: BarItem = { ...items.find((i) => i.key === "go:/projects")!, label: "Open tasks list", words: ["tasks"] };
    const r = (context: string) => rank({ items: [tasks, proj], query: "tasks", context, recent: [] })[0].key;
    expect(r("/tasks")).toBe("go:/tasks");
    expect(r("/projects")).toBe("go:/projects");
  });

  it("with no words, shows recent items first, then the jobs of this app", () => {
    const empty = rank({ items, query: "", context: "/email", recent: ["go:/people"] }).map((i) => i.key);
    expect(empty[0]).toBe("go:/people");
    expect(empty[1]).toBe("do:compose");
  });

  it("scores nothing for an empty query", () => {
    expect(score(items[0], "  ")).toBe(0);
  });
});

describe("recent items, per member", () => {
  const store = new Map<string, string>();
  beforeEach(() => {
    store.clear();
    (globalThis as { localStorage?: Pick<Storage, "getItem" | "setItem"> }).localStorage = {
      getItem: (k) => store.get(k) ?? null,
      setItem: (k, v) => void store.set(k, v),
    };
  });
  afterEach(() => {
    delete (globalThis as { localStorage?: unknown }).localStorage;
  });

  it("keeps the newest first, once each, and at most eight", () => {
    for (let i = 0; i < 10; i++) rememberRecent("a@x.test", `k${i}`);
    rememberRecent("a@x.test", "k3");
    const list = readRecent("a@x.test");
    expect(list[0]).toBe("k3");
    expect(list).toHaveLength(8);
    expect(new Set(list).size).toBe(8);
  });

  it("keeps two members apart on one browser", () => {
    rememberRecent("a@x.test", "go:/email");
    expect(readRecent("b@y.test")).toEqual([]);
  });

  it("survives junk in storage", () => {
    store.set("cc-shell-recent::a@x.test", "{not json");
    expect(readRecent("a@x.test")).toEqual([]);
  });
});
