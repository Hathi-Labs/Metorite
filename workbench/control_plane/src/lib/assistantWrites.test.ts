/**
 * Task #124 — the assistant's tool writes reach every live app through the one
 * path: `useAgentChat` -> `assistantWrites` -> `wireLiveRefresh` ->
 * `useLiveRefresh(run, app)`. Pure, no DOM.
 */
import fs from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { APP_REQUESTS_KEY, requestKey } from "@/app/integrations/lib/appRequests";
import { LIVE_PANES } from "@/lib/nav";
import { PLACEMENT } from "@/lib/chatPlacement";
import { clearAll, peek, put } from "@/lib/dataCache";
import { STUDIO_CARD_TOOLS } from "@/lib/studio/studioCardState";

import {
  APPS_WITHOUT_WRITE_TOOLS,
  APPS_WITH_WRITE_TOOLS,
  ASSISTANT_WROTE_EVENT,
  STUDIO_RECORD_WRITES,
  appsForWrite,
  eventNamesApp,
  noteToolEnd,
  noteToolStart,
} from "./assistantWrites";
import { wireLiveRefresh } from "./liveRefresh";

const SRC = path.join(__dirname, "..");
const read = (p: string) => fs.readFileSync(path.join(SRC, p), "utf-8");

/** Where each app calls `useLiveRefresh(..., "<app>")`. */
const WIRING: Record<string, string> = {
  projects: "app/projects/page.tsx",
  tasks: "app/tasks/page.tsx",
  calendar: "app/calendar/page.tsx",
  email: "app/email/page.tsx",
  crm: "app/crm/page.tsx",
  knowledge: "app/knowledge/layout.tsx",
  artifacts: "components/ArtifactSidebar.tsx",
  studio: "app/studio/page.tsx",
};

describe("which tool changes which app", () => {
  it("every write tool of the chat map names an app, but the profile tools", () => {
    for (const [name, place] of Object.entries(PLACEMENT)) {
      if (place !== "write") continue;
      if (name.startsWith("profile_")) {
        expect(appsForWrite(name), name).toEqual([]);
      } else {
        expect(appsForWrite(name).length, name).toBeGreaterThan(0);
      }
    }
  });

  it("a read changes no app", () => {
    for (const [name, place] of Object.entries(PLACEMENT)) {
      if (place === "evidence") expect(appsForWrite(name), name).toEqual([]);
    }
    expect(appsForWrite("unknown_tool")).toEqual([]);
  });

  it("each app has its own tools", () => {
    expect(appsForWrite("create_task")).toEqual(["projects", "tasks", "calendar"]);
    expect(appsForWrite("my_tasks_schedule")).toEqual(["projects", "tasks", "calendar"]);
    expect(appsForWrite("update_deal_status")).toEqual(["crm"]);
    expect(appsForWrite("draft_reply")).toEqual(["email"]);
    expect(appsForWrite("propose_page")).toEqual(["knowledge"]);
    expect(appsForWrite("write_artifact")).toEqual(["artifacts"]);
    expect(appsForWrite("skill__create_task")).toEqual(["projects", "tasks", "calendar"]);
  });
});

describe("the announcement", () => {
  it("names the apps, and only for a write that succeeded", () => {
    const got: unknown[] = [];
    const g = globalThis as unknown as Record<string, unknown>;
    const had = { window: g.window, CustomEvent: g.CustomEvent };
    g.CustomEvent = class { type: string; detail: unknown; constructor(t: string, i: { detail: unknown }) { this.type = t; this.detail = i.detail; } };
    g.window = { dispatchEvent: (e: { type: string; detail: unknown }) => got.push([e.type, e.detail]) };
    try {
      noteToolStart("a", "update_deal_status");
      expect(noteToolEnd("a", undefined, false)).toEqual([]);
      noteToolStart("b", "update_deal_status");
      expect(noteToolEnd("b", undefined, true)).toEqual(["crm"]);
      expect(noteToolEnd("c", "search_crm", true)).toEqual([]);
      expect(got).toEqual([[ASSISTANT_WROTE_EVENT, { apps: ["crm"], tool: "update_deal_status" }]]);
    } finally {
      g.window = had.window;
      g.CustomEvent = had.CustomEvent;
    }
  });

  it("eventNamesApp reads the detail", () => {
    expect(eventNamesApp({ apps: ["crm"] }, "crm")).toBe(true);
    expect(eventNamesApp({ apps: ["crm"] }, "email")).toBe(false);
    expect(eventNamesApp(undefined, "email")).toBe(false);
  });
});

describe("wireLiveRefresh and the assistant event", () => {
  function rig() {
    const handlers: Record<string, Array<(e?: unknown) => void>> = {};
    const add = (t: string, f: (e?: unknown) => void) => (handlers[t] ??= []).push(f);
    const remove = (t: string, f: (e?: unknown) => void) => {
      handlers[t] = (handlers[t] ?? []).filter((x) => x !== f);
    };
    const doc = { visibilityState: "visible", addEventListener: add, removeEventListener: remove };
    const win = { addEventListener: add, removeEventListener: remove };
    const fire = (t: string, detail?: unknown) => (handlers[t] ?? []).slice().forEach((f) => f({ detail }));
    return { doc, win, fire, handlers };
  }

  /** Timers a test runs by hand. */
  function hand() {
    const queue = new Map<number, () => void>();
    let next = 0;
    return {
      timers: {
        set: (fn: () => void) => { next += 1; queue.set(next, fn); return next; },
        clear: (h: unknown) => { queue.delete(h as number); },
      },
      flush: () => { const fns = [...queue.values()]; queue.clear(); fns.forEach((f) => f()); },
      size: () => queue.size,
    };
  }

  it("reads once the writes settle, when the event names this app", () => {
    const r = rig();
    const t = hand();
    let n = 0;
    wireLiveRefresh(() => n++, { doc: r.doc, win: r.win, app: "crm", timers: t.timers });
    r.fire(ASSISTANT_WROTE_EVENT, { apps: ["crm"] });
    expect(n).toBe(0);
    t.flush();
    expect(n).toBe(1);
    r.fire(ASSISTANT_WROTE_EVENT, { apps: ["email"] });
    t.flush();
    expect(n).toBe(1);
  });

  it("a burst of twenty writes folds into ONE read", () => {
    const r = rig();
    const t = hand();
    let n = 0;
    wireLiveRefresh(() => n++, { doc: r.doc, win: r.win, app: "projects", timers: t.timers });
    for (let i = 0; i < 20; i += 1) r.fire(ASSISTANT_WROTE_EVENT, { apps: ["projects"] });
    expect(t.size()).toBe(1);
    t.flush();
    expect(n).toBe(1);
  });

  it("a surface with no app ignores the event, and cleanup removes it", () => {
    const r = rig();
    let n = 0;
    const off = wireLiveRefresh(() => n++, { doc: r.doc, win: r.win });
    r.fire(ASSISTANT_WROTE_EVENT, { apps: ["crm"] });
    expect(n).toBe(0);
    off();
    expect(Object.values(r.handlers).every((h) => h.length === 0)).toBe(true);
  });
});

describe("the Studio pack (D179)", () => {
  it("a studio write tool names the studio app, and a studio read tool names none", () => {
    for (const w of ["studio_save", "studio_publish", "studio_skill_save", "studio_skill_state",
      // STU-B2c-2b: a resend after the approval writes a record, so an open app re-reads.
      "studio_record_create", "studio_record_update", "studio_record_delete"]) {
      expect(appsForWrite(w), w).toEqual(["studio"]);
    }
    for (const rd of ["studio_draft", "studio_validate", "studio_patch", "studio_outline", "studio_plan",
      "studio_describe", "studio_list_actions", "studio_package_get", "studio_record_list",
      "studio_skill_list", "studio_interview"]) {
      expect(appsForWrite(rd), rd).toEqual([]);
    }
  });

  it("a finished studio_save fires the refresh for studio, and a read does not", () => {
    const seen: unknown[] = [];
    const prev = (globalThis as { window?: unknown }).window;
    (globalThis as { window?: unknown }).window = {
      dispatchEvent: (e: { detail: unknown }) => { seen.push(e.detail); return true; },
    };
    try {
      noteToolStart("a1", "studio_save");
      expect(noteToolEnd("a1", undefined, true)).toEqual(["studio"]);
      noteToolStart("a2", "studio_describe");
      expect(noteToolEnd("a2", undefined, true)).toEqual([]);
      noteToolStart("a3", "studio_save");
      expect(noteToolEnd("a3", undefined, false)).toEqual([]);
    } finally {
      (globalThis as { window?: unknown }).window = prev;
    }
    expect(seen).toEqual([{ apps: ["studio"], tool: "studio_save" }]);
  });
});

describe("every live app with write tools is wired (the fence for #124)", () => {
  it("each app in the table calls useLiveRefresh with its own name", () => {
    for (const app of APPS_WITH_WRITE_TOOLS) {
      const file = WIRING[app];
      expect(file, `${app} has no wiring row`).toBeTruthy();
      expect(read(file), `${app}: ${file}`).toMatch(new RegExp(`useLiveRefresh\\([\\s\\S]*?"${app}"\\)`));
    }
  });

  it("every live pane is either wired or named as having no write tools", () => {
    for (const p of LIVE_PANES) {
      const slug = p.href.split("/")[1];
      const known = APPS_WITH_WRITE_TOOLS.some((a) => a === slug) || slug in APPS_WITHOUT_WRITE_TOOLS;
      // These panes have no assistant write tool at all (not a data app, or the
      // write goes through a surface that owns its own refresh).
      const OTHER = new Set(["", "dashboard", "whatsapp", "chat", "approvals", "integrations", "settings", "people"]);
      expect(known || OTHER.has(slug) || slug === "", `${p.href} is live and in no table`).toBe(true);
    }
  });

  it("the chat stream's live loop calls the one notifier", () => {
    const hook = read("hooks/useAgentChat.ts");
    expect(hook).toContain("noteToolEnd(");
    expect(hook).toContain("noteToolStart(");
    // The reconnect loop replays old events and must not announce.
    const replay = hook.slice(hook.indexOf("Mirror the live loop"));
    expect(replay.slice(0, 6000)).not.toContain("noteToolEnd(");
  });

  it("the old path stays: Projects still hears a receipt card", () => {
    expect(read("lib/liveRefresh.ts")).toContain("PROJECTS_CHANGED_EVENT");
  });
});

describe("a record write of Mira reads the approval cards again (STU-B2c-2c)", () => {
  // The re-review's P3: after a saved resend, the card still showed
  // "Approved" and "Tell the assistant" until the window took focus. The
  // write seam now drops the cached request reads, so each card reads its
  // request again. Mutation: drop the invalidate in announceAssistantWrite ->
  // the first case goes red.
  const RID = "5b1e3c2a-7d4f-4a6b-9c8d-0e1f2a3b4c5d";
  const seed = () => {
    clearAll();
    put(requestKey(RID), { request_id: RID, state: "approved" });
    put(APP_REQUESTS_KEY, { rows: [], more: 0 });
  };

  it("names the three record writes that the card draws", () => {
    expect([...STUDIO_RECORD_WRITES].sort()).toEqual([...STUDIO_CARD_TOOLS].sort());
    expect(requestKey(RID).startsWith(APP_REQUESTS_KEY)).toBe(true);
  });

  it("a saved resend drops the read of each request and of the list", () => {
    for (const tool of STUDIO_CARD_TOOLS) {
      seed();
      noteToolStart(`w-${tool}`, tool);
      expect(noteToolEnd(`w-${tool}`, undefined, true)).toEqual(["studio"]);
      expect(peek(requestKey(RID)), tool).toBeUndefined();
      expect(peek(APP_REQUESTS_KEY), tool).toBeUndefined();
    }
    clearAll();
  });

  it("a read, another studio write and a failed call keep the reads", () => {
    for (const [tool, ok] of [["studio_record_list", true], ["studio_save", true],
      ["studio_record_create", false]] as const) {
      seed();
      noteToolStart(`r-${tool}`, tool);
      noteToolEnd(`r-${tool}`, undefined, ok);
      expect(peek(requestKey(RID)), tool).toBeDefined();
    }
    clearAll();
  });
});
