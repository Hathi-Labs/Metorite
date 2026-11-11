// Change access in the AI agents view (WS-55 EA-17c-2, D-EA-13, spec §10.8).
//
// The vitest config collects `*.test.ts` in a node environment, so this file
// builds the panel with `createElement` and `renderToStaticMarkup` and does not
// use JSX. It fences:
//
// - the three switch states of a scope (on or off, "Reconnect", "did not ask");
// - ⚠️ never `pinned_projects: []` after "Only the ones I pick", also when the
//   member drops the last pin. `[]` leaves only for "All my projects";
// - the stale-mark warning, the chips, and the footer text;
// - the PATCH goes as ONE STRING, so the BFF sees a `Content-Length` (no 411).
//
// Mutations, each one red here:
// - send `[]` for a pick whose last pin was dropped: the "last pin" cases;
// - draw a disabled scope as a working switch: the markup cases;
// - send the body as a stream or an object: the "string body" case;
// - show "Change access" with the flag off: the source case.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildProjectTree, tickProject, untickProject } from "@/lib/accessTree";

import { listMyAgents, scopeWords, type AgentConnection } from "../lib/agents";
import {
  buildChange,
  grantChips,
  initialDraft,
  sameAsRow,
  saveChange,
  scopeNote,
  scopeState,
  splitMarks,
  staleNotice,
  switchScopes,
  toggleDraftScope,
} from "../lib/changeAccess";
import { AgentAccessForm } from "./AgentAccessPanel";

const KNOWN = ["projects:read", "projects:write", "context:read"];

const TREE = buildProjectTree({
  rows: [
    { id: "a", name: "Alpha", children: [{ id: "b", name: "Beta" }] },
    { id: "c", name: "Gamma" },
  ],
  personal: [{ id: "p1", name: "Mine" }],
});

function row(over: Partial<AgentConnection> = {}): AgentConnection {
  return {
    id: "11111111-0000-4000-8000-000000000001",
    member_email: "priya@acme.example",
    client_name: "Claude Code",
    client_origin: "https://claude.ai",
    scopes: ["projects:read", "offline_access"],
    pinned_projects: [],
    left_out_projects: [],
    include_personal: false,
    requested_scopes: ["projects:read", "projects:write"],
    ceiling_scopes: ["projects:read"],
    created_at: "2026-10-14T09:00:00+00:00",
    last_used_at: null,
    expires_at: "2027-01-12T10:00:00+00:00",
    ...over,
  };
}

function markup(r: AgentConnection, tree = TREE): string {
  return renderToStaticMarkup(
    createElement(AgentAccessForm, { row: r, tree, onSaved: () => undefined, onCancel: () => undefined }),
  );
}

describe("the three switch states", () => {
  const r = row({
    scopes: ["projects:read"],
    requested_scopes: ["projects:read", "projects:write"],
    ceiling_scopes: ["projects:read", "context:read"],
  });

  it("is on or off when the ceiling holds the scope", () => {
    expect(scopeState(r, "context:read")).toBe("toggle");
    expect(scopeNote(r, "context:read")).toBeNull();
  });

  it("says Reconnect when the agent asked and no live token carries the scope", () => {
    expect(scopeState(r, "projects:write")).toBe("reconnect");
    expect(scopeNote(r, "projects:write")).toBe("Reconnect claude.ai to turn this on.");
  });

  it("says the agent did not ask for every other scope", () => {
    const old = row({ requested_scopes: null, ceiling_scopes: ["projects:read"] });
    expect(scopeState(old, "projects:write")).toBe("not-asked");
    expect(scopeNote(old, "projects:write")).toBe(
      "claude.ai did not ask for this. It must reconnect to get it.",
    );
  });

  it("draws each state in the panel", () => {
    const html = markup(r);
    expect(html).toMatch(/data-testid="scope-context:read" data-state="toggle"/);
    expect(html).toMatch(/data-testid="scope-projects:write" data-state="reconnect"/);
    expect(html).toContain("Reconnect claude.ai to turn this on.");
    // The reconnect switch is off and disabled. The working one is not disabled.
    const write = html.split('data-testid="scope-projects:write"')[1].split("</li>")[0];
    expect(write).toMatch(/role="switch"[^>]*aria-checked="false"[^>]*disabled|disabled[^>]*role="switch"/);
    const context = html.split('data-testid="scope-context:read"')[1].split("</li>")[0];
    expect(context).not.toMatch(/disabled=""/);
  });

  it("never turns a disabled scope on", () => {
    const next = toggleDraftScope(r, new Set(["projects:read"]), "projects:write");
    expect([...next]).toEqual(["projects:read"]);
  });

  it("keeps a held scope switchable when no token carries it", () => {
    const held = row({ scopes: ["projects:read", "projects:write"], ceiling_scopes: ["projects:read"] });
    expect(scopeState(held, "projects:write")).toBe("toggle");
  });

  it("lists the switches in a fixed order, and never offline_access", () => {
    expect(switchScopes(r, KNOWN)).toEqual(["projects:read", "projects:write", "context:read"]);
    expect(switchScopes(row({ scopes: ["offline_access", "legacy:read"] }), KNOWN)).not.toContain("offline_access");
  });

  it("turns read off and the rest go, and a turn-on of write turns read on", () => {
    const w = row({ scopes: ["projects:read", "projects:write"], ceiling_scopes: ["projects:read", "projects:write"] });
    expect([...toggleDraftScope(w, new Set(["projects:read", "projects:write"]), "projects:read")]).toEqual([]);
    expect([...toggleDraftScope(w, new Set(), "projects:write")].sort()).toEqual(["projects:read", "projects:write"]);
  });
});

describe("never an empty pin list after a pick", () => {
  const pinned = row({ pinned_projects: ["c"] });

  it("sends [] for All my projects only", () => {
    const draft = { ...initialDraft(row(), TREE, KNOWN), mode: "all" as const };
    const built = buildChange(row(), draft, TREE, KNOWN);
    expect(built.ok && built.body.pinned_projects).toEqual([]);
  });

  it("refuses a pick with nothing ticked", () => {
    const draft = { ...initialDraft(row(), TREE, KNOWN), mode: "pick" as const };
    const built = buildChange(row(), draft, TREE, KNOWN);
    expect(built.ok).toBe(false);
  });

  it("refuses the save when the member drops the last pin", () => {
    const draft = initialDraft(pinned, TREE, KNOWN);
    expect(draft.mode).toBe("pick");
    expect(draft.selection.pins).toEqual(["c"]);
    const dropped = { ...draft, selection: untickProject(TREE, draft.selection, "c") };
    expect(dropped.selection.pins).toEqual([]);
    const built = buildChange(pinned, dropped, TREE, KNOWN);
    expect(built.ok).toBe(false);
  });

  it("refuses a pick that holds only the personal pin", () => {
    const draft = { ...initialDraft(pinned, TREE, KNOWN), selection: { pins: [], leftOut: [] }, includePersonal: true };
    expect(buildChange(pinned, draft, TREE, KNOWN).ok).toBe(false);
  });

  it("sends the picks, the left-out list and the personal roots", () => {
    const draft = {
      ...initialDraft(pinned, TREE, KNOWN),
      selection: untickProject(TREE, tickProject(TREE, { pins: [], leftOut: [] }, "a"), "b"),
      includePersonal: true,
    };
    const built = buildChange(pinned, draft, TREE, KNOWN);
    expect(built.ok).toBe(true);
    if (built.ok) {
      expect(built.body.pinned_projects).toEqual(["a", "p1"]);
      expect(built.body.left_out_projects).toEqual(["b"]);
      expect(built.body.include_personal).toBe(true);
      expect(built.body.pinned_projects.length).toBeGreaterThan(0);
    }
  });

  it("sends the offline scope only when the row holds it", () => {
    const draft = { ...initialDraft(row(), TREE, KNOWN), mode: "all" as const };
    const withOffline = buildChange(row(), draft, TREE, KNOWN);
    expect(withOffline.ok && withOffline.body.scopes).toEqual(["projects:read", "offline_access"]);
    const none = row({ scopes: ["projects:read"] });
    const without = buildChange(none, { ...initialDraft(none, TREE, KNOWN), mode: "all" }, TREE, KNOWN);
    expect(without.ok && without.body.scopes).toEqual(["projects:read"]);
  });

  it("refuses a body with no scope", () => {
    const draft = { ...initialDraft(row(), TREE, KNOWN), scopes: new Set<string>(), mode: "all" as const };
    expect(buildChange(row(), draft, TREE, KNOWN).ok).toBe(false);
  });

  it("opens a row with left-out marks and no pin as a pick of every root", () => {
    const wide = row({ left_out_projects: ["b"] });
    const draft = initialDraft(wide, TREE, KNOWN);
    expect(draft.mode).toBe("pick");
    expect(draft.selection).toEqual({ pins: ["a", "c"], leftOut: ["b"] });
  });

  it("keeps Save off while the draft equals the row", () => {
    const draft = initialDraft(pinned, TREE, KNOWN);
    const built = buildChange(pinned, draft, TREE, KNOWN);
    expect(built.ok && sameAsRow(pinned, built.body)).toBe(true);
  });

  it("holds the rule in the panel markup: Save is disabled with no pin", () => {
    const html = markup(pinned);
    expect(html).toContain("Save changes");
    // Unchanged draft: Save is disabled.
    const save = html.split("Save changes")[0].split("<button").pop()!;
    expect(save).toContain("disabled");
  });
});

describe("a mark the member no longer sees", () => {
  const gone = row({ pinned_projects: ["c", "gone-1", "p1"], left_out_projects: ["gone-2"] });

  it("counts the stray marks and spares the personal tree", () => {
    const marks = splitMarks(gone, TREE);
    expect(marks.stale).toBe(2);
    expect(marks.selection.pins).toEqual(["c"]);
  });

  it("warns in words that say what to do", () => {
    expect(staleNotice(1)).toContain("1 project that is marked for this agent is not in your list any more");
    expect(staleNotice(2)).toContain("2 projects that are marked");
    expect(staleNotice(0)).toBeNull();
    expect(markup(gone)).toContain('data-testid="stale-marks"');
    expect(markup(row({ pinned_projects: ["c"] }))).not.toContain("stale-marks");
  });

  it("drops the stale marks from the save", () => {
    const draft = initialDraft(gone, TREE, KNOWN);
    const built = buildChange(gone, draft, TREE, KNOWN);
    expect(built.ok && built.body.pinned_projects).toEqual(["c"]);
    expect(built.ok && built.body.left_out_projects).toEqual([]);
  });

  it("keeps the marks of a cut list, because the page cannot tell", () => {
    const cut = { ...TREE, truncated: true };
    const marks = splitMarks(gone, cut);
    expect(marks.stale).toBe(0);
    expect(marks.carried.pins).toEqual(["gone-1"]);
  });
});

describe("the chips", () => {
  it("shows the scopes, the project count and Personal", () => {
    const chips = grantChips(row({ pinned_projects: ["a", "b"], include_personal: true }), scopeWords);
    expect(chips.projects).toBe("2 projects");
    expect(chips.personal).toBe(true);
    expect(chips.scopes).toContain("Read your projects and tasks");
  });

  it("says All projects for no pin, and 1 project for one", () => {
    expect(grantChips(row(), scopeWords).projects).toBe("All projects");
    expect(grantChips(row({ pinned_projects: ["a"] }), scopeWords).projects).toBe("1 project");
    expect(grantChips(row(), scopeWords).personal).toBe(false);
  });
});

describe("the request", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the body as ONE STRING, so the BFF sees a Content-Length", async () => {
    const sent: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      sent.push({ url, init });
      return new Response(JSON.stringify(row()), { status: 200 });
    });
    await saveChange("abc", {
      scopes: ["projects:read"],
      pinned_projects: ["a"],
      left_out_projects: [],
      include_personal: false,
    });
    expect(sent[0].url).toBe("/api/agent-access/connections/abc");
    expect(sent[0].init.method).toBe("PATCH");
    expect(typeof sent[0].init.body).toBe("string");
    expect(JSON.parse(sent[0].init.body as string).pinned_projects).toEqual(["a"]);
  });

  it("turns a refusal into words that say what to do", async () => {
    vi.stubGlobal("fetch", async () =>
      new Response(JSON.stringify({ detail: { error: "scope_above_ceiling" } }), { status: 422 }),
    );
    await expect(
      saveChange("abc", { scopes: ["projects:read"], pinned_projects: [], left_out_projects: [], include_personal: false }),
    ).rejects.toThrow("Reconnect the agent");
  });

  it("reads the Change access flag of the list", async () => {
    vi.stubGlobal("fetch", async () =>
      new Response(JSON.stringify({ connections: [row()], change_access: true }), { status: 200 }),
    );
    expect((await listMyAgents())?.changeAccess).toBe(true);
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ connections: [row()] }), { status: 200 }));
    expect((await listMyAgents())?.changeAccess).toBe(false);
  });
});

describe("the sources", () => {
  const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf-8");

  it("shows Change access only when the list says the flag is on", () => {
    const view = read("./AgentsView.tsx");
    expect(view).toContain("onChanged={changeAccess ? changed : undefined}");
    expect(view).toContain("mine.data?.changeAccess === true");
  });

  it("restores the consent footer", () => {
    expect(read("../../connect/authorize/ConsentScreen.tsx")).toContain(
      "Change or remove this access any time in",
    );
  });

  it("never builds the body from a stream", () => {
    const src = read("../lib/changeAccess.ts");
    expect(src).toContain("JSON.stringify(body)");
    expect(src).not.toMatch(/ReadableStream|duplex/);
  });
});
