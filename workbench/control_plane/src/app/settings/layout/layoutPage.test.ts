// PKG-17c-2. Settings, Home layout.
//
// Spec: `project-docs/specs/package_host.md` §9.1 and §10, "PKG-17c-2".
// vitest here runs in the node environment, so the pure view is drawn with
// `renderToStaticMarkup`. The wiring is held by source scans.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EMPTY_ORG_LAYOUT, type OrgLayout } from "@/lib/shell/orgLayout";
import { MAX_PINS, toBody } from "@/lib/shell/orgLayoutAdmin";
import { STARRABLE } from "@/lib/shell/presets";

import { HomeLayoutView, type ViewProps } from "./HomeLayoutForm";
import {
  CAP_TEXT,
  CONFIRM_BODY,
  commitDraft,
  needsConfirm,
  pickPreset,
  sameLayout,
  starrablePanes,
  togglePin,
  clearRole,
} from "./homeLayout";

const noop = () => {};
function view(over: Partial<ViewProps>): string {
  const props: ViewProps = {
    read: { kind: "ok", layout: EMPTY_ORG_LAYOUT, empty: false },
    draft: EMPTY_ORG_LAYOUT,
    dirty: false,
    busy: false,
    error: "",
    refusal: "",
    saved: false,
    confirmOpen: false,
    onToggle: noop,
    onPreset: noop,
    onReset: noop,
    onSave: noop,
    onRetry: noop,
    onConfirm: noop,
    onCancel: noop,
    ...over,
  };
  return renderToStaticMarkup(createElement(HomeLayoutView, props));
}

const read = (rel: string) => readFileSync(join(__dirname, rel), { encoding: "utf-8" });
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

describe("the states", () => {
  it("draws five roles, a Save button and the helper line when the read is ok", () => {
    const html = view({});
    for (const r of ["Owner", "Admin", "Manager", "Member", "Guest"]) expect(html).toContain(`>${r}<`);
    expect(html.indexOf(">Owner<")).toBeLessThan(html.indexOf(">Guest<"));
    expect(html).toContain("A member who does not hold an app still does not see it.");
    expect(html).toContain("Use the system default");
    expect(html).toContain(">Save<");
    expect(html).toMatch(/<button[^>]*disabled[^>]*>[\s\S]*?Save/);
  });

  it("lists only starrable panes", () => {
    expect(starrablePanes().length).toBeGreaterThan(3);
    for (const p of starrablePanes()) expect(STARRABLE.has(p.href)).toBe(true);
  });

  it("shows a card and no form when the feature is off (404)", () => {
    const html = view({ read: { kind: "off" } });
    expect(html).toContain("Not turned on for your organisation.");
    expect(html).not.toContain("<form");
    expect(html).not.toContain("checkbox");
    expect(html).not.toContain(">Save<");
  });

  it("shows Retry and no Save at all on a fault", () => {
    const html = view({ read: { kind: "fault" }, dirty: true });
    expect(html).toContain("Could not read the default.");
    expect(html).toContain("Retry");
    expect(html).not.toContain("Save");
    expect(html).not.toContain("checkbox");
  });

  it("shows a skeleton while the read is out", () => {
    const html = view({ read: { kind: "loading" } });
    expect(html).toContain('aria-busy="true"');
    expect(html).not.toContain("Save");
  });

  it("shows the gateway reason, the cap sentence and Saved", () => {
    expect(view({ error: "Pane not known: /x" })).toContain("Pane not known: /x");
    expect(view({ refusal: CAP_TEXT })).toContain(CAP_TEXT);
    expect(view({ saved: true })).toContain("Saved");
  });

  it("asks before the first save of an empty read", () => {
    // The dialog draws in a portal, which a static render skips. The wiring is read from the source.
    const form = code(read("HomeLayoutForm.tsx"));
    expect(form).toMatch(/<ConfirmDialog[\s\S]*?open=\{p\.confirmOpen\}[\s\S]*?body=\{CONFIRM_BODY\}[\s\S]*?confirmLabel="Save"[\s\S]*?cancelLabel="Cancel"/);
    expect(CONFIRM_BODY).toBe("This replaces the whole default. The read showed none. Save only if you mean it.");
    expect(needsConfirm(true, false)).toBe(true);
    expect(needsConfirm(true, true)).toBe(false);
    expect(needsConfirm(false, false)).toBe(false);
  });
});

describe("the draft", () => {
  const pane = [...STARRABLE][0];

  it("stars a pane and unstars it back to no entry", () => {
    const on = togglePin(EMPTY_ORG_LAYOUT, "member", pane, true).layout;
    expect(on.roles.member?.pins).toEqual([pane]);
    expect(sameLayout(on, EMPTY_ORG_LAYOUT)).toBe(false);
    const off = togglePin(on, "member", pane, false).layout;
    expect(off.roles.member).toBeUndefined();
    expect(sameLayout(off, EMPTY_ORG_LAYOUT)).toBe(true);
  });

  it("ignores a pane that is not starrable, an install href too", () => {
    const c = togglePin(EMPTY_ORG_LAYOUT, "member", "/apps/acme/invoices", true);
    expect(c.layout).toBe(EMPTY_ORG_LAYOUT);
  });

  it("refuses a pick over the cap with a sentence", () => {
    const full: OrgLayout = { roles: { guest: { preset: null, pins: Array.from({ length: MAX_PINS }, (_, i) => `/p${i}`) } } };
    // The cap is checked on the draft length, so a hand-built full draft is enough.
    const c = togglePin(full, "guest", pane, true);
    expect(c.refusal).toBe(CAP_TEXT);
    expect(c.layout).toBe(full);
    expect(CAP_TEXT).toBe(`A role can star at most ${MAX_PINS} apps.`);
  });

  it("sets a preset, and resets one role", () => {
    const withPreset = pickPreset(EMPTY_ORG_LAYOUT, "admin", "engineer");
    expect(withPreset.roles.admin?.preset).toBe("engineer");
    expect(pickPreset(withPreset, "admin", "").roles.admin).toBeUndefined();
    expect(clearRole(withPreset, "admin").roles.admin).toBeUndefined();
  });
});

describe("the save", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends toBody of the draft, and an untouched role goes back unchanged", async () => {
    const pane = [...STARRABLE][0];
    const loaded: OrgLayout = { roles: { guest: { preset: "new-hire", pins: [pane] } } };
    const draft = pickPreset(loaded, "admin", "engineer");
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => ({
      ok: true,
      status: 200,
      json: async () => JSON.parse(String(init?.body)),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const res = await commitDraft(draft);
    expect(res.kind).toBe("saved");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/shell/layout");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual(toBody(draft));
    expect(JSON.parse(String(init.body)).roles.guest).toEqual({ preset: "new-hire", pins: [pane] });
  });

  it("returns the gateway reason on an error", async () => {
    vi.stubGlobal("fetch", async () => ({ ok: false, status: 422, json: async () => ({ detail: "Too many apps" }) }));
    expect(await commitDraft(EMPTY_ORG_LAYOUT)).toEqual({ kind: "error", status: 422, reason: "Too many apps" });
  });
});

describe("the page", () => {
  it("shows a read-only card to a member without the capability, and the form only to an admin", () => {
    const src = code(read("page.tsx"));
    expect(src).toContain("hasCapability(access, ORG_WRITE_CAPABILITY)");
    expect(src).toMatch(/canManage\s*\?\s*\(\s*<HomeLayoutForm\s*\/>/);
    expect(src).toContain("READ_ONLY_TEXT");
    expect(read("homeLayout.ts")).toContain("Ask an admin to change the Home layout.");
  });

  it("uses the shared controls, with no raw button and no hex colour", () => {
    for (const f of ["page.tsx", "HomeLayoutForm.tsx", "homeLayout.ts"]) {
      const src = code(read(f));
      expect(src, f).not.toMatch(/<button\b/);
      expect(src, f).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    }
    const form = read("HomeLayoutForm.tsx");
    for (const c of ["ui/Button", "ui/Checkbox", "ui/SelectButton", "ui/ConfirmDialog", "ui/Skeleton"]) {
      expect(form).toContain(`@/components/${c}`);
    }
  });

  it("opens from the Organisation bar", () => {
    const src = readFileSync(join(__dirname, "..", "organization", "OrganizationAdmin.tsx"), { encoding: "utf-8" });
    expect(src).toMatch(/href="\/settings\/layout"[\s\S]{0,300}Home layout/);
  });
});
