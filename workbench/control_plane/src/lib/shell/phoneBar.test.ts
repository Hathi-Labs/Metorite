/**
 * The fence of the phone's bottom bar (MOB-12, task 2005; R7).
 *
 * `phoneBar.ts` holds the rule. This file makes breaking it fail:
 *   - every live pane resolves to an entry, and a `none` gives its reason;
 *   - a live bar holds at most 3 tabs, counted with the Ask tab present, and
 *     at most one accent, and only an act is the accent;
 *   - every label fits 12 characters, every id is unique, every icon is real;
 *   - every `event` is answered in some file under `src/app`, and every
 *     `href` is a route folder under `src/app`;
 *   - the shell holds no branch of its own for an app.
 *
 * The runner is `environment: "node"`, so these read source.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";
import { LIVE_PANES } from "@/lib/nav";

import {
  PHONE_BARS,
  hrefSelected,
  phoneBarFor,
  projectsBarTabs,
  resolvePhoneTabs,
  type PhoneTab,
} from "./phoneBar";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const APP = join(SRC, "app");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) out.push(p);
  }
  return out;
}
const APP_FILES = walk(APP).map((f) => readFileSync(f, "utf-8"));
const APP_SOURCE = APP_FILES.join("\n");
// Only a file that listens on the bar's channel can answer a tab's event. A
// quoted string anywhere else is not an answer (MOB-12 fix round 2).
const LISTENER_SOURCE = APP_FILES.filter((src) => /addEventListener\(\s*["']cc-mobile-nav["']/.test(src)).join("\n");

const everyone = { ask: true, allowed: () => true };

/** Every tab list of an entry, with the Projects flag off and on. */
function tabLists(path: string): PhoneTab[][] {
  const reg = phoneBarFor(path);
  if (!reg || !("tabs" in reg.entry)) return [];
  if (reg.app === "Projects") return [projectsBarTabs(false), projectsBarTabs(true)];
  return [typeof reg.entry.tabs === "function" ? reg.entry.tabs() : reg.entry.tabs];
}

describe("the registry resolves every live pane", () => {
  it.each(LIVE_PANES.map((p) => [p.label, p.href] as const))("%s (%s) has an entry", (_l, href) => {
    expect(phoneBarFor(href), href).not.toBeNull();
  });

  it("My Day, the home page, has an entry", () => {
    expect(phoneBarFor("/")).not.toBeNull();
  });

  it("a `none` entry gives its reason in words", () => {
    const nones = PHONE_BARS.filter((r) => "none" in r.entry);
    expect(nones.length).toBeGreaterThan(0);
    for (const r of nones) {
      const reason = "none" in r.entry ? r.entry.none : "";
      expect(reason.length, r.app).toBeGreaterThanOrEqual(20);
    }
  });

  it("the longest prefix wins, and `/` matches only itself", () => {
    expect(phoneBarFor("/")?.app).toBe("My Day");
    expect(phoneBarFor("/people/chart")?.app).toBe("People");
    expect(phoneBarFor("/people/me")?.app).toBe("People");
    expect(phoneBarFor("/settings/appearance")?.app).toBe("Appearance");
    expect(phoneBarFor("/settings/organization")?.app).toBe("Organisation");
    expect(phoneBarFor("/build/apps/x/edit")?.app).toBe("Workshop");
    expect(phoneBarFor("/build/apps/x")).toBeNull();
    expect(phoneBarFor("/notes")?.app).toBe("Notes");
    expect(phoneBarFor("/notes/abc")).toBeNull();
    expect(phoneBarFor("/crm/leads")?.app).toBe("CRM");
    expect(phoneBarFor("/dashboard")).toBeNull();
  });
});

describe("the rule", () => {
  const livePaths = LIVE_PANES.map((p) => p.href);

  it.each(livePaths)("%s: at most 3 tabs with Ask present, one accent, only an act", (href) => {
    const shown = resolvePhoneTabs(phoneBarFor(href), href, everyone);
    expect(shown.length, href).toBeLessThanOrEqual(3);
    for (const list of tabLists(href)) {
      const accents = list.filter((t) => t.accent);
      expect(accents.length, href).toBeLessThanOrEqual(1);
      for (const t of accents) expect(t.kind, `${href} ${t.id}`).toBe("act");
    }
  });

  it("the Projects bar holds at most 3 tabs with the flag off and on", () => {
    for (const on of [false, true]) {
      const tabs = projectsBarTabs(on);
      expect(tabs.length).toBeLessThanOrEqual(3);
      expect(tabs.filter((t) => t.accent).length).toBeLessThanOrEqual(1);
    }
  });

  it("every other bar keeps one accent at most, and the accent is an act", () => {
    for (const r of PHONE_BARS) {
      if (!("tabs" in r.entry)) continue;
      const list = typeof r.entry.tabs === "function" ? r.entry.tabs() : r.entry.tabs;
      const accents = list.filter((t) => t.accent);
      expect(accents.length, r.app).toBeLessThanOrEqual(1);
      for (const t of accents) expect(t.kind, `${r.app} ${t.id}`).toBe("act");
    }
  });

  it("a tab has a short label, a real icon, a unique id and one way to act", () => {
    for (const r of PHONE_BARS) {
      if (!("tabs" in r.entry)) continue;
      const lists = r.app === "Projects" ? [projectsBarTabs(false), projectsBarTabs(true)] : tabLists(r.path ?? "/build/apps/x/edit");
      for (const list of lists) {
        const ids = new Set<string>();
        for (const t of list) {
          expect(t.label.length, `${r.app} ${t.id}`).toBeLessThanOrEqual(12);
          expect(isKnownIcon(t.icon), `${r.app} ${t.icon}`).toBe(true);
          expect(ids.has(t.id), `${r.app} duplicate ${t.id}`).toBe(false);
          ids.add(t.id);
          expect(Boolean(t.href) !== Boolean(t.event), `${r.app} ${t.id}: href or event, not both`).toBe(true);
        }
      }
    }
  });

  it("the tabs without Ask are the tabs with Ask, plus the ones that stand in for it", () => {
    const tasks = phoneBarFor("/tasks");
    const withAsk = resolvePhoneTabs(tasks, "/tasks", everyone).map((t) => t.id);
    const without = resolvePhoneTabs(tasks, "/tasks", { ...everyone, ask: false }).map((t) => t.id);
    expect(withAsk).toEqual(["tasks-inbox", "tasks-lists", "tasks-capture"]);
    expect(without).toEqual([...withAsk, "tasks-assistant"]);
    // `/chat` with the shell chat on is a door to the Ask sheet, so its tabs need Ask absent.
    const chat = phoneBarFor("/chat");
    expect(resolvePhoneTabs(chat, "/chat", everyone)).toEqual([]);
    expect(resolvePhoneTabs(chat, "/chat", { ...everyone, ask: false }).map((t) => t.id)).toEqual(["chats", "files", "chat-new"]);
    const email = phoneBarFor("/email");
    expect(resolvePhoneTabs(email, "/email", everyone).map((t) => t.id)).not.toContain("email-ai");
    expect(resolvePhoneTabs(email, "/email", { ...everyone, ask: false }).map((t) => t.id)).toContain("email-ai");
  });
});

describe("every tab goes somewhere", () => {
  const all: Array<[string, PhoneTab]> = [];
  for (const r of PHONE_BARS) {
    if (!("tabs" in r.entry)) continue;
    const lists = r.app === "Projects" ? [projectsBarTabs(false), projectsBarTabs(true)] : [typeof r.entry.tabs === "function" ? r.entry.tabs() : r.entry.tabs];
    for (const l of lists) for (const t of l) all.push([r.app, t]);
  }

  it("each `event` appears quoted in a file under src/app that listens on cc-mobile-nav", () => {
    for (const [app, t] of all) {
      if (!t.event) continue;
      expect(LISTENER_SOURCE.includes(`"${t.event}"`), `${app}: no listener of "cc-mobile-nav" answers "${t.event}"`).toBe(true);
    }
  });

  it("each `href` is a route folder under src/app", () => {
    for (const [app, t] of all) {
      if (!t.href) continue;
      const dir = join(APP, ...t.href.split("/").filter(Boolean));
      expect(existsSync(join(dir, "page.tsx")), `${app}: ${t.href} has no page`).toBe(true);
    }
  });

  it("a link tab is selected by its path, whole", () => {
    const [chart] = all.filter(([, t]) => t.id === "people-chart").map(([, t]) => t);
    const [dir] = all.filter(([, t]) => t.id === "people-directory").map(([, t]) => t);
    expect(hrefSelected(chart, "/people/chart")).toBe(true);
    expect(hrefSelected(dir, "/people/chart")).toBe(false);
    expect(hrefSelected(dir, "/people")).toBe(true);
  });
});

describe("the page's report filters the tabs", () => {
  it("hide drops a tab, and a gate drops a tab", () => {
    const email = phoneBarFor("/email");
    const hidden = resolvePhoneTabs(email, "/email", { ...everyone, hide: ["email-accounts", "email-automation", "email-compose", "email-ai"] });
    expect(hidden).toEqual([]);
    const people = phoneBarFor("/people");
    const noHr = resolvePhoneTabs(people, "/people", { ask: true, allowed: (t) => !t.capability });
    expect(noHr.map((t) => t.id)).toEqual(["people-directory", "people-chart"]);
    const none = resolvePhoneTabs(people, "/people", { ask: true, allowed: (t) => !t.feature });
    expect(none).toEqual([]);
  });

  it("Triage shows on /whatsapp only, Sections on every page of it", () => {
    const wa = phoneBarFor("/whatsapp");
    expect(resolvePhoneTabs(wa, "/whatsapp", everyone).map((t) => t.id)).toEqual(["wa-triage", "wa-sections"]);
    expect(resolvePhoneTabs(phoneBarFor("/whatsapp/calls"), "/whatsapp/calls", everyone).map((t) => t.id)).toEqual(["wa-sections"]);
  });
});

describe("the shell holds no branch of its own for an app", () => {
  const shell = readFileSync(join(SRC, "components", "AppShell.tsx"), "utf-8");
  it.each([
    "isEmailPage",
    "isChatPage",
    "isTasksPage",
    "isNotesPage",
    "isWhatsAppPage",
    "isWhatsAppInbox",
    "isAppWorkshopEditPage",
    "isProjectsPage",
    "cc-tasks-section",
    "cc-email-empty",
  ])("AppShell.tsx does not name %s", (name) => {
    expect(shell.includes(name), name).toBe(false);
  });

  it("the shell keeps one listener for the one page event", () => {
    expect(shell.split(`addEventListener(PHONE_BAR_EVENT`).length - 1).toBe(1);
    expect(readFileSync(join(SRC, "lib", "shell", "phoneBarEvent.ts"), "utf-8")).toContain('"cc-phone-bar"');
  });

  it("a selected place, the accent and the rest never wear the same look", () => {
    // MOB-12 (owner, 2026-11-07): a selected place, the accent and Ask all
    // drew in one blue, so three tabs looked chosen at once. One `BarIcon`
    // now draws each tab, Menu and Ask too: a tinted pill for "where I am",
    // a filled circle for "what I can make", and no colour on the tab root.
    expect(shell).toMatch(/selected: "font-semibold text-foreground"/);
    expect(shell).toMatch(/look === "selected" \? "bg-primary\/15 text-primary"/);
    expect(shell).toMatch(/rounded-full bg-primary text-primary-foreground/);
    expect(shell).toMatch(/const BAR_TAB =\s*"[^"]*text-muted-foreground[^"]*"/);
    expect(shell).not.toMatch(/const BAR_TAB =\s*"[^"]*text-primary/);
    // Menu, every app tab and Ask go through the one well.
    expect(shell.split("<BarIcon look=").length - 1).toBe(3);
  });

  it("no page sends the retired events", () => {
    expect(APP_SOURCE).not.toContain("cc-tasks-section");
    expect(APP_SOURCE).not.toContain("cc-email-empty");
  });
});

describe("MOB-12 fix round 2: a page reports what it cannot answer", () => {
  const page = (p: string) => readFileSync(join(APP, p), "utf-8");

  it("Knowledge hides the three tabs in every state with no rail", () => {
    const layout = page("knowledge/layout.tsx");
    expect(layout).toMatch(/usePhoneBar\(\{ hide: NO_RAIL_TABS \}\)/);
    expect(layout).toContain('["knowledge-rail", "knowledge-search", "knowledge-new-page"]');
  });

  it("Integrations hides Views for a member with one view or none, once access has loaded", () => {
    expect(page("integrations/page.tsx")).toContain('!accessLoading && views.length <= 1 ? ["integrations-views"]');
  });

  it("a member who is not an admin sees no Organisation tab", () => {
    expect(page("settings/organization/OrganizationAdmin.tsx")).toContain('["org-members", "org-requests", "org-invite"]');
  });

  it("Reminders hides only when the flag is off", () => {
    expect(page("calendar/CalendarView.tsx")).toContain('remList.status === "absent" ? ["calendar-reminders"]');
  });

  it("on a phone the Knowledge and Integrations titles are text, not a second Spaces or Views button", () => {
    expect(page("knowledge/layout.tsx")).toContain('const title = "Knowledge";');
    expect(page("integrations/page.tsx")).toContain('title={isMobile ? current.label : "Integrations"}');
  });
});
