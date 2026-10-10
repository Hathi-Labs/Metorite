import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { PANES, visibleSections, type NavPane } from "@/lib/nav";
import { pinnedPanes } from "./presets";
import {
  JOBS,
  buildItems,
  contextPane,
  heldPanes,
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
  team: "personal",
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

describe("an admin who names the roster opens it, not the invite form", () => {
  const org = PANES.find((p) => p.href === "/settings/organization")!;
  const items = buildItems([org], true);
  const top = (query: string) => rank({ items, query, context: null, recent: [] })[0]?.key;

  it("opens Organisation for the roster's own words", () => {
    expect(top("members")).toBe("go:/settings/organization");
    expect(top("seats")).toBe("go:/settings/organization");
  });
  it("offers the invite form for an invite", () => {
    expect(top("invite")).toBe("do:invite");
    expect(top("invite a member")).toBe("do:invite");
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

describe("the empty bar follows the member's preset (NS-7)", () => {
  const items = buildItems(HELD);
  const jobs = (order?: string[], context: string | null = null) =>
    rank({ items, query: "", context, recent: [], order }).filter((i) => i.group === "do").map((i) => i.key);

  it("puts the preset's jobs first, in its order", () => {
    const first = jobs(["find-person", "compose"]);
    expect(first.slice(0, 2)).toEqual(["do:find-person", "do:compose"]);
  });

  it("keeps every other held job after them", () => {
    expect([...jobs(["compose"])].sort()).toEqual([...jobs()].sort());
  });

  it("still puts the app the member is in before the preset", () => {
    expect(jobs(["compose"], "/tasks")[0]).toBe("do:capture");
  });

  it("changes nothing once the member types", () => {
    const typed = (order?: string[]) => rank({ items, query: "email", context: null, recent: [], order }).map((i) => i.key);
    expect(typed(["find-person"])).toEqual(typed());
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

describe("the purpose line is behind the shell nav flag (NS-2)", () => {
  // The bar is ON in production. A new line here reaches every organization
  // at once, so it ships dark like the rest of NS-2.
  const approvals = PANES.find((p) => p.href === "/approvals")!;

  it("with the flag off, prints the note and matches on the note, as before", () => {
    const go = buildItems([approvals], false).find((i) => i.key === "go:/approvals")!;
    expect(go.hint).toBe(approvals.note);
    expect(go.words).not.toContain("send");
  });

  it("with the flag on, prints the purpose and matches on its words too", () => {
    const go = buildItems([approvals], true).find((i) => i.key === "go:/approvals")!;
    expect(go.hint).toBe(approvals.blurb);
    expect(go.words).toContain("send");
  });

  it("reads the flag by default, and the flag is off in a test", () => {
    const go = buildItems([approvals]).find((i) => i.key === "go:/approvals")!;
    expect(go.hint).toBe(approvals.note);
  });
});

/**
 * The bar finds every app the member holds, pinned or not (owner,
 * 2026-10-11). The sidebar shows only the pinned apps, so an app the member
 * did not pin has one door: the command bar. A member who pinned nothing
 * must still find all of them.
 *
 * Mutations, seen red first: build the bar from `pinnedPanes(pins, …)` in
 * `ShellBar.tsx`, cut `heldPanes` to a part of the list, or drop its Center
 * filter. Each one fails a case below.
 */
describe("every app the member holds, with nothing pinned", () => {
  const LIVE_FEATURES = ["tasks", "email", "whatsapp", "projects", "people", "chat", "approvals"];
  // Preview panes the member also holds the grant for. They stay out.
  const PREVIEW_FEATURES = ["crm", "notes", "dashboard", "workflows", "agents", "integrations"];
  const goHrefs = (features: string[], admin: boolean, preview = false) =>
    buildItems(heldPanes(visibleSections(features, admin, preview)))
      .filter((i) => i.group === "go")
      .map((i) => i.href);
  const top = (features: string[], admin: boolean, query: string) =>
    rank({ items: buildItems(heldPanes(visibleSections(features, admin, false))), query, context: null, recent: [] })
      .filter((i) => i.group === "go")
      .map((i) => i.href);

  it("an admin who pinned nothing finds all eleven live panes, Admin and AI Studio too", () => {
    const sections = visibleSections([...LIVE_FEATURES, ...PREVIEW_FEATURES], true, false);
    // The sidebar's own input, with no pins: it holds nothing to draw.
    expect(pinnedPanes([], sections)).toEqual([]);
    expect(goHrefs([...LIVE_FEATURES, ...PREVIEW_FEATURES], true)).toEqual([
      "/tasks",
      "/calendar",
      "/people/me",
      "/email",
      "/whatsapp",
      "/projects",
      "/people",
      "/chat",
      "/approvals",
      "/settings/organization",
      "/settings/appearance",
    ]);
  });

  it("a member gets no pane they lack, and no admin pane", () => {
    expect(goHrefs(["tasks", "chat"], false)).toEqual([
      "/tasks",
      "/calendar",
      "/people/me",
      "/chat",
      "/settings/appearance",
    ]);
  });

  it("finds each app by a part of its name", () => {
    const admin = [...LIVE_FEATURES];
    expect(top(admin, true, "peo")).toContain("/people");
    expect(top(admin, true, "email")).toContain("/email");
    expect(top(admin, true, "appro")).toContain("/approvals");
    expect(top(admin, true, "orga")[0]).toBe("/settings/organization");
    expect(top(admin, true, "chat")).toContain("/chat");
    expect(top(admin, true, "whats")).toContain("/whatsapp");
    expect(top(admin, true, "cal")).toContain("/calendar");
  });

  it("finds nothing for an app the member lacks", () => {
    const member = ["tasks", "chat"];
    expect(top(member, false, "peo")).toEqual([]);
    expect(top(member, false, "appro")).toEqual([]);
    expect(top(member, false, "orga")).toEqual([]);
    expect(top(member, false, "email")).toEqual([]);
  });

  it("never offers a preview pane, even to a member who holds its grant", () => {
    const preview = new Set(PANES.filter((p) => p.launch === "preview").map((p) => p.href));
    const go = goHrefs([...LIVE_FEATURES, ...PREVIEW_FEATURES], true);
    expect(go.filter((h) => preview.has(h))).toEqual([]);
  });

  it("never links to a Center, even with the preview flag on (D49)", () => {
    const centerFeatures = PANES.filter((p) => p.href.startsWith("/centers/")).map((p) => p.feature!);
    expect(centerFeatures.length).toBeGreaterThan(0);
    const go = goHrefs([...LIVE_FEATURES, ...centerFeatures], true, true);
    expect(go.filter((h) => h.startsWith("/centers/"))).toEqual([]);
    // The flag still restores the other preview panes, as in the sidebar.
    expect(goHrefs(["crm"], false, true)).toContain("/crm");
  });

  it("the shell bar hands the bar every held pane, never the pins", () => {
    const src = readFileSync(fileURLToPath(new URL("./ShellBar.tsx", import.meta.url)), "utf8");
    expect(src).toMatch(/heldPanes\(visibleSections\(loading \? null : access\.features, access\.is_admin\)\)/);
    expect(src).not.toMatch(/pinnedPanes|shellSidebar|layout\.pins/);
  });
});
