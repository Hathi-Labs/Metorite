/**
 * The guide runner, one test per state and move (WS-OB GD-4, fence F4,
 * `assistant_guide.md` §9). The vitest environment is node with no DOM, so the
 * store is driven with a fake storage and the rest is pure.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { guideAct } from "./assistant";
import { assistantFor, showsPopover } from "./GuideHost";
import { GuideStepCardBody } from "./GuideStepCard";
import { OWN_PART, bindAnchorClick, bindEscape, pickVisible } from "./GuideSpotlight";
import {
  ANCHOR_MISSING_NOTE,
  GUIDE_STORAGE_KEY,
  TOUR_CHIP_MAX,
  TOUR_GUIDE_ID,
  createGuideRunner,
  guideFromSearch,
  parseStored,
  tourChips,
  withoutGuideParam,
  type GuideSource,
} from "./guide";
import { GENERATED_GUIDES, type Guide } from "./guides.generated";
import { visibleSections } from "@/lib/nav";
import { starrableHeld } from "./presets";

const TOUR: Guide = {
  id: "t.tour",
  app: "t",
  feature: null,
  adminOnly: false,
  title: "Take the tour",
  asks: [],
  steps: [
    { kind: "point", say: "One.", anchor: "home.app-list" },
    { kind: "open", say: "Two.", path: "/projects" },
    { kind: "point", say: "Three.", anchor: "home.ask" },
    { kind: "say", say: "Four." },
  ],
  doTool: null,
};
const BAD_OPEN: Guide = { ...TOUR, id: "t.bad", steps: [{ kind: "open", say: "Go.", path: "//evil.com" }] };

function rig(guides: readonly Guide[] = [TOUR, BAD_OPEN], allowed: boolean | null = true) {
  const data = new Map<string, string>();
  const warns: string[] = [];
  const logs: { event: string; data: Record<string, unknown> }[] = [];
  const runner = createGuideRunner({
    allowed,
    guides,
    storage: () => ({
      getItem: (k) => data.get(k) ?? null,
      setItem: (k, v) => void data.set(k, v),
      removeItem: (k) => void data.delete(k),
    }),
    warn: (m) => warns.push(m),
    log: (event, d) => logs.push({ event, data: d }),
    isPath: (h) => h.startsWith("/") && !h.startsWith("//"),
  });
  return { runner, data, warns, logs };
}
const stored = (data: Map<string, string>) => JSON.parse(data.get(GUIDE_STORAGE_KEY) ?? "null");

describe("F4: a guide starts only on a member act", () => {
  it("no guide starts with no member act", () => {
    const { runner, data } = rig();
    // A mount with no mark in the address and none in storage starts nothing.
    expect(runner.resume()).toBe(false);
    expect(runner.get().phase).toBe("idle");
    expect(data.size).toBe(0);
  });

  it("only a member entry calls startGuide: the address link and the chip", () => {
    const SRC = fileURLToPath(new URL("../..", import.meta.url));
    const callers: string[] = [];
    const walk = (dir: string) => {
      for (const e of readdirSync(dir)) {
        const full = join(dir, e);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.tsx?$/.test(e) && !/\.test\.tsx?$/.test(e)) {
          const code = readFileSync(full, "utf-8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
          if (/\bstartGuide\(/.test(code) && !full.endsWith(`${sep}guide.ts`)) {
            callers.push(relative(SRC, full).split(sep).join("/"));
          }
        }
      }
    };
    walk(SRC);
    // GD-5a: the founder's "Show me around" and the account menu's "Take the
    // tour" (the foot menu and the phone drawer). Each one is a click, so each
    // is a member act.
    expect(callers.sort()).toEqual([
      "components/AccountSwitcher.tsx",
      "components/WelcomeDialog.tsx",
      "lib/shell/AssistantDock.tsx",
      "lib/shell/GuideHost.tsx",
    ]);
  });

  it("the host starts only from the address, and reads storage otherwise", () => {
    const host = readFileSync(join(__dirname, "GuideHost.tsx"), "utf-8");
    expect(host).toContain('startGuide(id, "url")');
    expect(host).toContain("resumeGuide()");
  });

  it("an unknown id is ignored with a warning, and nothing is stored", () => {
    const { runner, warns, data } = rig();
    expect(runner.start("nope.tour", "url")).toBe(false);
    expect(runner.get().phase).toBe("idle");
    expect(warns).toHaveLength(1);
    expect(data.size).toBe(0);
  });
});

describe("the states and moves", () => {
  it("start: a point step is pointing at step 0, and storage holds it", () => {
    const { runner, data } = rig();
    expect(runner.start("t.tour", "button")).toBe(true);
    const s = runner.get();
    expect([s.phase, s.guideId, s.step, s.total, s.source]).toEqual(["pointing", "t.tour", 0, 4, "button"]);
    expect(stored(data)).toEqual({ guideId: "t.tour", step: 0 });
    expect(runner.view()?.anchor).toBe("home.app-list");
  });

  it("open: the step waits for its route, pushes once, and points when the page arrives", () => {
    const { runner } = rig();
    runner.routeChanged("/");
    runner.start("t.tour", "button");
    runner.next();
    expect(runner.get().phase).toBe("opening");
    expect(runner.get().push).toBe("/projects");
    runner.pushed();
    expect(runner.get().push).toBeNull();
    expect(runner.get().phase).toBe("opening");
    runner.routeChanged("/projects");
    expect(runner.get().phase).toBe("pointing");
  });

  it("open: a member already on the page gets no push", () => {
    const { runner } = rig();
    runner.routeChanged("/projects");
    runner.start("t.tour", "button");
    runner.next();
    expect(runner.get().phase).toBe("pointing");
    expect(runner.get().push).toBeNull();
  });

  it("open: a path that is not an app path is a missing step and is never pushed", () => {
    const { runner, logs } = rig();
    runner.start("t.bad", "button");
    expect(runner.get().phase).toBe("missing");
    expect(runner.get().push).toBeNull();
    expect(logs[0].event).toBe("guide_anchor_missing");
  });

  it("a member who opens the next step's page moves the tour on", () => {
    const { runner } = rig();
    runner.routeChanged("/");
    runner.start("t.tour", "button");
    runner.routeChanged("/projects");
    expect(runner.get().step).toBe(1);
    expect(runner.get().phase).toBe("pointing");
    expect(runner.get().push).toBeNull();
  });

  it("missing: the words stay, the miss is logged with guide, step and anchor, and Next still works", () => {
    const { runner, logs } = rig();
    runner.start("t.tour", "button");
    runner.anchorMissing("home.app-list");
    expect(runner.get().phase).toBe("missing");
    expect(runner.view()).toMatchObject({ say: "One.", missing: true });
    expect(logs).toEqual([{ event: "guide_anchor_missing", data: { guide: "t.tour", step: 0, anchor: "home.app-list" } }]);
    runner.next();
    expect(runner.get().step).toBe(1);
  });

  it("missing: an anchor of another step is ignored", () => {
    const { runner, logs } = rig();
    runner.start("t.tour", "button");
    runner.anchorMissing("home.ask");
    expect(runner.get().phase).toBe("pointing");
    expect(logs).toHaveLength(0);
  });

  it("next and back move one step, and back stops at the first", () => {
    const { runner, data } = rig();
    runner.routeChanged("/projects");
    runner.start("t.tour", "button");
    runner.back();
    expect(runner.get().step).toBe(0);
    runner.next();
    runner.next();
    expect(runner.get().step).toBe(2);
    expect(stored(data).step).toBe(2);
    runner.back();
    expect(runner.get().step).toBe(1);
  });

  it("a say step shows words and no anchor", () => {
    const { runner } = rig();
    runner.routeChanged("/projects");
    runner.start("t.tour", "button");
    runner.next();
    runner.next();
    runner.next();
    expect(runner.view()).toMatchObject({ say: "Four.", anchor: null, isLast: true });
  });

  it("the last Next ends the guide as done and clears storage", () => {
    const { runner, data } = rig();
    runner.routeChanged("/projects");
    runner.start("t.tour", "button");
    for (let i = 0; i < 3; i++) runner.next();
    runner.next();
    expect(runner.get().phase).toBe("ended");
    expect(runner.get().reason).toBe("done");
    expect(data.size).toBe(0);
  });

  it("End ends it, the page is untouched (no push), and storage is cleared", () => {
    const { runner, data } = rig();
    runner.start("t.tour", "button");
    runner.end("end");
    expect(runner.get()).toMatchObject({ phase: "ended", reason: "end", push: null });
    expect(data.size).toBe(0);
    expect(runner.view()).toBeNull();
  });

  it("a guide can start again after it ended", () => {
    const { runner } = rig();
    runner.start("t.tour", "button");
    runner.end("end");
    runner.start("t.tour", "url");
    expect(runner.get()).toMatchObject({ phase: "pointing", step: 0, source: "url" });
  });
});

describe("F4: Escape removes the ring in the same frame", () => {
  it("one Escape keydown ends the tour before the handler returns", () => {
    const { runner, data } = rig();
    runner.start("t.tour", "button");
    let handler: ((e: Event) => void) | null = null;
    const doc = {
      addEventListener: (_t: string, h: (e: Event) => void) => void (handler = h),
      removeEventListener: () => {},
    };
    bindEscape(doc as never, () => runner.end("escape"));
    handler!({ key: "Escape" } as unknown as Event);
    // Synchronous: no timer, no exit move. The host unmounts the ring on this change.
    expect(runner.get()).toMatchObject({ phase: "ended", reason: "escape" });
    expect(runner.view()).toBeNull();
    expect(data.size).toBe(0);
  });

  it("the host binds Escape once, with no timer", () => {
    const host = readFileSync(join(__dirname, "GuideHost.tsx"), "utf-8");
    expect(host).toContain('bindEscape(document, () => endGuide("escape"))');
    expect(host).not.toMatch(/setTimeout|requestAnimationFrame/);
  });

  it("a listener is told at once", () => {
    const { runner } = rig();
    runner.start("t.tour", "button");
    const seen: string[] = [];
    runner.subscribe(() => seen.push(runner.get().phase));
    runner.end("escape");
    expect(seen).toEqual(["ended"]);
  });
});

describe("resume from sessionStorage", () => {
  it("a reload resumes at the stored step, and an open step is not pushed again", () => {
    const { runner, data } = rig();
    data.set(GUIDE_STORAGE_KEY, JSON.stringify({ guideId: "t.tour", step: 1 }));
    expect(runner.resume()).toBe(true);
    expect(runner.get()).toMatchObject({ phase: "pointing", step: 1, source: "resume", push: null });
  });

  it.each([
    ["not JSON", "{oops"],
    ["a number", "5"],
    ["an unknown guide", JSON.stringify({ guideId: "gone", step: 0 })],
    ["a step out of range", JSON.stringify({ guideId: "t.tour", step: 9 })],
    ["a fractional step", JSON.stringify({ guideId: "t.tour", step: 0.5 })],
    ["a string step", JSON.stringify({ guideId: "t.tour", step: "1" })],
  ])("a broken value (%s) is ignored and removed", (_n, raw) => {
    const { runner, data } = rig();
    data.set(GUIDE_STORAGE_KEY, raw);
    expect(runner.resume()).toBe(false);
    expect(runner.get().phase).toBe("idle");
    expect(data.has(GUIDE_STORAGE_KEY)).toBe(false);
  });

  it("parseStored reads a good value", () => {
    expect(parseStored(JSON.stringify({ guideId: "t.tour", step: 3 }), [TOUR])).toEqual({ guideId: "t.tour", step: 3 });
    expect(parseStored(null, [TOUR])).toBeNull();
  });
});

describe("the address entry", () => {
  it("reads ?guide= and drops only that parameter", () => {
    expect(guideFromSearch("?guide=home.tour")).toBe("home.tour");
    expect(guideFromSearch("?a=1")).toBeNull();
    expect(guideFromSearch("?guide=")).toBeNull();
    expect(withoutGuideParam("/", "?guide=home.tour", "")).toBe("/");
    expect(withoutGuideParam("/projects", "?guide=x&task=4", "#a")).toBe("/projects?task=4#a");
  });
});

describe("the agent entry: the guide event", () => {
  const base = { open: true, threadId: "a", activeId: "a", guideId: "home.tour" };
  it("starts beside the side panel and leaves a chip on a phone or in the full view", () => {
    expect(guideAct({ ...base, phone: false, form: "dock" })).toBe("start");
    expect(guideAct({ ...base, phone: false, form: "full" })).toBe("offer");
    expect(guideAct({ ...base, phone: true, form: "full" })).toBe("offer");
    expect(guideAct({ ...base, phone: true, form: "peek" })).toBe("offer");
  });
  it("does nothing for a closed assistant, another chat or an empty id", () => {
    expect(guideAct({ ...base, open: false, phone: false, form: "dock" })).toBe("none");
    expect(guideAct({ ...base, threadId: "b", phone: false, form: "dock" })).toBe("none");
    expect(guideAct({ ...base, guideId: "", phone: false, form: "dock" })).toBe("none");
  });
  it("a chip waits for a tap and then starts with source agent", () => {
    const { runner } = rig();
    expect(runner.offer("t.tour")).toBe(true);
    expect(runner.get().phase).toBe("idle");
    expect(runner.get().offer?.title).toBe("Take the tour");
    runner.acceptOffer();
    expect(runner.get()).toMatchObject({ phase: "pointing", source: "agent", offer: null });
  });
  it("a chip for an unknown id is ignored with a warning", () => {
    const { runner, warns } = rig();
    expect(runner.offer("nope")).toBe(false);
    expect(runner.get().offer).toBeNull();
    expect(warns).toHaveLength(1);
  });
  it("the event is wired and draws nothing in the transcript", () => {
    const hook = readFileSync(join(__dirname, "../../hooks/useAgentChat.ts"), "utf-8");
    expect(hook).toContain('emitAgentEvent("onGuide"');
    expect(hook.match(/evtName !== GUIDE_EVENT/g)).toHaveLength(2);
  });
});

describe("where the assistant goes for a step (phone strip, spec 4.3)", () => {
  const f = { missing: false, open: false, mode: "dock" as const };
  it("a phone drops to the strip for a page anchor and rises to the sheet for a chat anchor", () => {
    expect(assistantFor({ ...f, phone: true, anchor: "home.ask" })).toBe("peek");
    expect(assistantFor({ ...f, phone: true, anchor: null })).toBe("peek");
    expect(assistantFor({ ...f, phone: true, anchor: "chat.panel" })).toBe("full");
    expect(assistantFor({ ...f, phone: true, anchor: "home.ask", open: true, mode: "peek" })).toBeNull();
    expect(assistantFor({ ...f, phone: true, anchor: "home.ask", open: true, mode: "full" })).toBe("peek");
  });
  it("a desktop opens the panel for a chat anchor, a bare step or a miss only", () => {
    expect(assistantFor({ ...f, phone: false, anchor: "chat.message-field" })).toBe("dock");
    expect(assistantFor({ ...f, phone: false, anchor: null })).toBe("dock");
    expect(assistantFor({ ...f, phone: false, anchor: "home.ask", missing: true })).toBe("dock");
    expect(assistantFor({ ...f, phone: false, anchor: "home.ask" })).toBeNull();
    expect(assistantFor({ ...f, phone: false, anchor: "chat.panel", open: true })).toBeNull();
  });
});

describe("the views", () => {
  it("the step card shows the line, 2 of 5, Back, End tour and Next", () => {
    const html = renderToStaticMarkup(
      createElement(GuideStepCardBody, {
        view: { say: "Type here.", index: 2, total: 5, missing: false },
        onBack: () => {},
        onNext: () => {},
        onEnd: () => {},
      }),
    );
    for (const t of ["Type here.", "2 of 5", "Back", "End tour", "Next"]) expect(html).toContain(t);
    expect(html).not.toContain("point at it");
  });
  it("a missing anchor adds the note, and the first step has no Back", () => {
    const html = renderToStaticMarkup(
      createElement(GuideStepCardBody, {
        view: { say: "Type here.", index: 1, total: 5, missing: true },
        onNext: () => {},
        onEnd: () => {},
      }),
    );
    expect(ANCHOR_MISSING_NOTE).toBe("I can't point at it on this screen.");
    expect(html).toContain("point at it on this screen.");
    expect(html).not.toContain("Back");
  });
  it("the first visible match of an anchor wins", () => {
    expect(pickVisible(["hidden", "shown", "also"], (e) => e !== "hidden")).toBe("shown");
    expect(pickVisible(["hidden"], () => false)).toBeNull();
  });
});

describe("a press on the ringed part moves on, but not a press on the guide's own parts", () => {
  it("ignores a press inside the step card, the strip or the popover", () => {
    let handler: ((e?: Event) => void) | null = null;
    const el = {
      addEventListener: (_t: string, h: (e?: Event) => void) => void (handler = h),
      removeEventListener: () => {},
    };
    let n = 0;
    bindAnchorClick(el as unknown as HTMLElement, () => void n++);
    handler!({ target: { closest: (sel: string) => (sel === OWN_PART ? {} : null) } } as unknown as Event);
    expect(n).toBe(0);
    handler!({ target: { closest: () => null } } as unknown as Event);
    expect(n).toBe(1);
    expect(OWN_PART).toContain("[data-guide-card]");
  });
});

describe("the shipped table", () => {
  it("the tour runs from its first step", () => {
    const data = new Map<string, string>();
    const runner = createGuideRunner({
      allowed: true,
      guides: GENERATED_GUIDES,
      storage: () => ({
        getItem: (k) => data.get(k) ?? null,
        setItem: (k, v) => void data.set(k, v),
        removeItem: (k) => void data.delete(k),
      }),
      warn: () => {},
      log: () => {},
      isPath: () => true,
    });
    expect(runner.start("home.tour", "button")).toBe(true);
    expect(runner.view()).toMatchObject({ index: 1, total: 5, anchor: "home.app-list" });
  });
});

describe("GD-5a: the switch (GUIDE_ORGS) gates every start in ONE place", () => {
  const SOURCES: GuideSource[] = ["button", "url", "agent", "welcome", "account-menu"];

  it("off refuses every source, the offer and the resume, and stores nothing", () => {
    const { runner, data } = rig([TOUR, BAD_OPEN], false);
    for (const s of SOURCES) expect(runner.start("t.tour", s)).toBe(false);
    expect(runner.get().phase).toBe("idle");
    expect(runner.offer("t.tour")).toBe(false);
    runner.acceptOffer();
    expect(runner.get().phase).toBe("idle");
    data.set(GUIDE_STORAGE_KEY, JSON.stringify({ guideId: "t.tour", step: 2 }));
    expect(runner.resume()).toBe(false);
    expect(runner.get().phase).toBe("idle");
    expect(runner.allowed()).toBe(false);
  });

  it("before the read answers, a start waits, and on starts it with its own source", () => {
    const { runner, data } = rig([TOUR, BAD_OPEN], null);
    expect(runner.start("t.tour", "url")).toBe(false);
    expect(runner.get().phase).toBe("idle");
    expect(data.size).toBe(0);
    runner.setAllowed(true);
    expect(runner.get()).toMatchObject({ phase: "pointing", guideId: "t.tour", step: 0, source: "url" });
  });

  it("before the read answers, a start waits, and off drops it", () => {
    const { runner, data } = rig([TOUR, BAD_OPEN], null);
    runner.start("t.tour", "url");
    runner.setAllowed(false);
    expect(runner.get().phase).toBe("idle");
    expect(data.size).toBe(0);
    // A later on does not bring the dropped start back.
    runner.setAllowed(true);
    expect(runner.get().phase).toBe("idle");
  });

  it("a resume after a reload waits for the read too", () => {
    const { runner, data } = rig([TOUR, BAD_OPEN], null);
    data.set(GUIDE_STORAGE_KEY, JSON.stringify({ guideId: "t.tour", step: 2 }));
    expect(runner.resume()).toBe(false);
    expect(runner.get().phase).toBe("idle");
    runner.setAllowed(true);
    expect(runner.get()).toMatchObject({ phase: "pointing", step: 2, source: "resume" });
  });

  it("an unknown id is refused at once, with its warning, whatever the switch", () => {
    const { runner, warns } = rig([TOUR, BAD_OPEN], null);
    expect(runner.start("nope.tour", "url")).toBe(false);
    expect(warns).toHaveLength(1);
    runner.setAllowed(true);
    expect(runner.get().phase).toBe("idle");
  });

  it("off after on ends a tour on screen, and removes an offer", () => {
    const a = rig();
    a.runner.start("t.tour", "welcome");
    expect(a.runner.get().source).toBe("welcome");
    a.runner.setAllowed(false);
    expect(a.runner.get()).toMatchObject({ phase: "ended", reason: "end" });
    expect(a.data.size).toBe(0);
    const b = rig();
    expect(b.runner.offer("t.tour")).toBe(true);
    b.runner.setAllowed(false);
    expect(b.runner.get().offer).toBeNull();
  });

  it("a new member (null) keeps a tour on screen and waits for their own read", () => {
    const { runner } = rig();
    runner.start("t.tour", "account-menu");
    runner.setAllowed(null);
    expect(runner.get().phase).toBe("pointing");
    expect(runner.start("t.tour", "button")).toBe(false);
    runner.setAllowed(true);
    expect(runner.get()).toMatchObject({ phase: "pointing", step: 0, source: "button" });
  });

  it("the singleton waits for the read: no read, no tour", () => {
    // Fail closed: a runner made without the switch treats it as not known.
    const runner = createGuideRunner({
      guides: [TOUR],
      storage: () => undefined,
      warn: () => {},
      log: () => {},
      isPath: () => true,
    });
    expect(runner.allowed()).toBeNull();
    expect(runner.start("t.tour", "button")).toBe(false);
    expect(runner.get().phase).toBe("idle");
    const guide = readFileSync(join(__dirname, "guide.ts"), "utf-8");
    expect(guide).not.toMatch(/allowed:\s*true/);
  });

  it("the shell read is the one writer of the switch", () => {
    const prefs = readFileSync(join(__dirname, "shellPrefs.ts"), "utf-8");
    expect(prefs).toContain("setGuideAllowed(guideOn(body));");
    expect(prefs).toContain("setGuideAllowed(null);");
  });
});

describe("GD-5a: the app chips of the tour's last stop", () => {
  const ALL = visibleSections(
    ["tasks", "projects", "email", "chat", "people", "approvals", "calendar", "crm", "notes", "whatsapp", "artifacts", "knowledge"],
    true,
    false,
  );

  it("the tour id is the shipped guide's", () => {
    expect(GENERATED_GUIDES.some((g) => g.id === TOUR_GUIDE_ID)).toBe(true);
  });

  it("at most six, in nav order, from the held live apps", () => {
    const held = starrableHeld(ALL);
    const chips = tourChips(held);
    expect(chips.length).toBe(Math.min(TOUR_CHIP_MAX, held.length));
    expect(chips.map((c) => c.href)).toEqual(held.slice(0, TOUR_CHIP_MAX).map((p) => p.href));
    for (const c of chips) expect(Object.keys(c).sort()).toEqual(["href", "icon", "label"]);
  });

  it("names no pane the member does not hold, and no preview pane", () => {
    const few = tourChips(starrableHeld(visibleSections(["tasks"], false, false)));
    expect(few.map((c) => c.href)).not.toContain("/projects");
    const preview = visibleSections(["crm"], false, true).flatMap((s) => s.items).filter((p) => p.launch === "preview");
    const live = tourChips(starrableHeld(ALL)).map((c) => c.href);
    for (const p of preview) expect(live).not.toContain(p.href);
    expect(tourChips(starrableHeld(visibleSections(null, false, false)))).toEqual([]);
  });

  it("the card draws one chip per app on the last stop, and none without", () => {
    const chips = [
      { href: "/tasks", label: "My Tasks", icon: "CheckSquare" },
      { href: "/projects", label: "Projects", icon: "FolderKanban" },
    ];
    const html = renderToStaticMarkup(
      createElement(GuideStepCardBody, {
        view: { say: "Try it.", index: 5, total: 5, missing: false },
        onNext: () => {},
        onEnd: () => {},
        chips,
        onChip: () => {},
      }),
    );
    expect(html).toContain("data-guide-chips");
    for (const t of ["My Tasks", "Projects", "5 of 5", "End tour"]) expect(html).toContain(t);
    const none = renderToStaticMarkup(
      createElement(GuideStepCardBody, {
        view: { say: "Try it.", index: 4, total: 5, missing: false },
        onNext: () => {},
        onEnd: () => {},
      }),
    );
    expect(none).not.toContain("data-guide-chips");
  });

  it("a chip ends the tour as done and opens its app, and marks the chapter seam", () => {
    const card = readFileSync(join(__dirname, "GuideStepCard.tsx"), "utf-8");
    expect(card).toContain("// GD-10: start the app's chapter");
    // Ended as done, the sheet out of the way on a phone (the chat link's
    // one rule), and then the app.
    expect(card).toMatch(/endGuide\("done"\);[\s\S]*?onChatLink\(\{ phone: isMobile, chatFirst: chatFirstOn\(\) \}\)[\s\S]*?router\.push\(c\.href\);/);
    expect(card).toContain("view.guide.id === TOUR_GUIDE_ID && view.isLast");
    // The phone strip stays one line: it draws no chip.
    const dock = readFileSync(join(__dirname, "AssistantDock.tsx"), "utf-8");
    expect(dock).not.toContain("tourChips");
  });
});

describe("GD-5a: the last stop's card is not covered", () => {
  it("the tour's last stop rings the message field with no popover, on any screen", () => {
    expect(showsPopover({ phone: false, anchor: "chat.message-field", tourLast: true })).toBe(false);
    expect(showsPopover({ phone: true, anchor: "chat.message-field", tourLast: true })).toBe(false);
  });
  it("every other stop keeps the GD-4 rule", () => {
    expect(showsPopover({ phone: false, anchor: "chat.panel", tourLast: false })).toBe(true);
    expect(showsPopover({ phone: false, anchor: "chat.message-field", tourLast: false })).toBe(true);
    expect(showsPopover({ phone: false, anchor: "home.ask", tourLast: true })).toBe(true);
    expect(showsPopover({ phone: true, anchor: "home.app-list", tourLast: false })).toBe(true);
    expect(showsPopover({ phone: true, anchor: "home.ask", tourLast: false })).toBe(false);
  });
});
