// The CRM layout of spec section 18.4 and the settings page that did not open
// (section 18.6), drawn (CRM-Q3). The runner has no DOM, so each component is
// rendered to a string and the page is read as text.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ModeSwitch } from "@/components/ModeSwitch";
import { toLeadLanes } from "../lib/board";
import { BOUNDARY_COPY, boundaryMessage } from "../lib/boundary";
import { wantsGroups } from "../lib/access";
import { zohoLink } from "../lib/connect";
import { NO_RELATED, relatedSections } from "../lib/related";
import type { DealPipeline, Lead, Status } from "../lib/types";
import CrmBoundary, { CrmFallback } from "./CrmBoundary";
import FieldsPanel from "./FieldsPanel";
import KanbanBoard from "./KanbanBoard";
import PipelineSettings from "./PipelineSettings";
import RelatedLists from "./RelatedLists";

const noop = () => undefined;
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

// ── The Leads board ───────────────────────────────────────────────────────

const status = (id: string, name: string, position: number): Status => ({
  id,
  name,
  color: "blue",
  position,
  type: "open",
  is_default: position === 0,
});

const lead = (id: string, name: string, ext: string | null): Lead =>
  ({ id, lead_name: name, status_id: "s1", ext_source: ext, owner_email: "vj@x.in" }) as Lead;

describe("the Leads board", () => {
  const statuses = [status("s2", "Contacted", 1), status("s1", "New", 0)];
  const lanes = (ext: string | null) =>
    toLeadLanes(statuses, { s1: { rows: [lead("l1", "Acme lead", ext)], total: 7 } });
  const draw = (over: { ext?: string | null; readOnly?: boolean; onCreate?: () => void } = {}) =>
    renderToStaticMarkup(
      createElement(KanbanBoard, {
        entity: "leads",
        lanes: lanes(over.ext ?? null),
        loading: false,
        readOnly: over.readOnly,
        onMove: noop,
        onOpen: noop,
        onCreate: over.onCreate,
      }),
    );

  it("draws one column per Lead Status, in order, with the status total", () => {
    const html = draw();
    expect(html.indexOf("New")).toBeLessThan(html.indexOf("Contacted"));
    expect(html).toContain("Acme lead");
    // The first lane header carries the count of the whole status, not the page.
    expect(html).toMatch(/>7<\/span>/);
    expect(html).not.toContain("IndianRupee");
  });

  it("lets a Metorite lead be dragged in Native mode", () => {
    expect(draw()).toContain('draggable="true"');
  });

  it("does not let a card drag in Mirror mode (the Z7 rule)", () => {
    // Zoho owns every record. The mode alone shuts the drag.
    const html = draw({ readOnly: true });
    expect(html).toContain('draggable="false"');
    expect(html).not.toContain('draggable="true"');
  });

  it("does not let a card with an ext_source drag, even in Native mode", () => {
    const html = draw({ ext: "zoho" });
    expect(html).toContain('draggable="false"');
    expect(html).not.toContain('draggable="true"');
  });

  it("names the create button for a lead", () => {
    expect(draw({ onCreate: noop })).toContain("New lead");
    expect(draw()).not.toContain("New lead");
  });

  it("says what is missing when there are no statuses", () => {
    const html = renderToStaticMarkup(
      createElement(KanbanBoard, { entity: "leads", lanes: [], loading: false, onMove: noop, onOpen: noop }),
    );
    expect(html).toContain("No lead statuses are set yet.");
  });
});

// ── The switch ────────────────────────────────────────────────────────────

describe("the list or board switch", () => {
  const draw = (mode: "list" | "board") =>
    renderToStaticMarkup(
      createElement(ModeSwitch<"list" | "board">, {
        modes: [
          { id: "list", icon: "List" },
          { id: "board", icon: "Kanban" },
        ],
        mode,
        onPick: noop,
        label: "Deals layout",
      }),
    );

  it("shows both choices and marks the one on screen", () => {
    const html = draw("board");
    expect(html).toContain("List");
    expect(html).toContain("Board");
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1);
  });

  it("is the shared control, drawn in the title bar tools for Deals and Leads only", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/from "@\/components\/ModeSwitch"/);
    expect(page).toMatch(/hasBoard\(view\.tab\) && \(/);
    expect(page).toMatch(/onPick=\{\(next\) => go\(selectLayout\(view, next\)\)\}/);
  });
});

// ── The picker is drawn from the pipeline count ───────────────────────────

describe("the pipeline picker in the page", () => {
  const page = read("../page.tsx");

  it("draws only for Deals and only with two pipelines or more", () => {
    expect(page).toMatch(/view\.tab === "deals" && showPicker\(pipelines\)/);
  });

  it("remembers the choice and writes it to the URL", () => {
    expect(page).toMatch(/remember\(id\)/);
    expect(page).toMatch(/selectPipeline\(view, id\)/);
  });

  it("waits for the pipeline list before the Deals board loads, so it loads once", () => {
    expect(page).toMatch(/view\.tab === "deals" && pipelinesLoading/);
  });
});

// ── Related lists ─────────────────────────────────────────────────────────

describe("related lists", () => {
  const related = {
    ...NO_RELATED,
    contacts: [{ id: "c1", first_name: "Anitha", last_name: "Kumar", title: "Buyer" }],
    deals: [{ id: "d1", name: "Bosch order", amount: 250000, currency: "INR", organization_name: "Bosch" }],
  };

  it("gives a company its contacts and deals, a contact its deals, and others none", () => {
    expect(relatedSections("organizations", related).map((s) => s.title)).toEqual(["Contacts", "Deals"]);
    expect(relatedSections("contacts", related).map((s) => s.title)).toEqual(["Deals"]);
    expect(relatedSections("leads", related)).toEqual([]);
    // A deal's contacts are the Contacts section of the fields panel.
    expect(relatedSections("deals", related)).toEqual([]);
  });

  it("draws each row, and a row opens that record", () => {
    const html = renderToStaticMarkup(
      createElement(RelatedLists, { entity: "organizations", related, onOpen: noop }),
    );
    expect(html).toContain("Anitha Kumar");
    expect(html).toContain("Buyer");
    expect(html).toContain("Bosch order");
    expect(html).toMatch(/<button[^>]*type="button"[^>]*>/);
  });

  it("says so when a list is empty", () => {
    const html = renderToStaticMarkup(
      createElement(RelatedLists, { entity: "contacts", related: NO_RELATED, onOpen: noop }),
    );
    expect(html).toContain("This contact is on no deal.");
  });

  it("opens the record from the sheet with the existing sheet parameter", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/onOpenRelated=\{\(entity, id\) => go\(openRecord\(view, PARAM_FOR\[entity\], id\)\)\}/);
    expect(page).toMatch(/related=\{store\.related\}/);
  });

  it("shows a deal its contacts with their roles", () => {
    const html = renderToStaticMarkup(
      createElement(FieldsPanel, {
        entity: "deals",
        record: { id: "d1", name: "Bosch order" },
        statuses: [],
        dealContacts: [
          { contact: { id: "c1", first_name: "Anitha", last_name: "Kumar" }, role: "Procurement", is_primary: true },
        ],
        saving: false,
        onPatch: noop,
        onMoveStatus: noop,
        onConvert: noop,
        onSetPrimary: noop,
        onDetach: noop,
        onOpenContact: noop,
      } as never),
    );
    expect(html).toContain("Contacts");
    expect(html).toContain("Anitha Kumar");
    expect(html).toContain("Procurement");
  });

  it("mounts the sections in the fields panel for a company", () => {
    const html = renderToStaticMarkup(
      createElement(FieldsPanel, {
        entity: "organizations",
        record: { id: "o1", name: "Bosch" },
        statuses: [],
        dealContacts: [],
        saving: false,
        onPatch: noop,
        onMoveStatus: noop,
        onConvert: noop,
        onSetPrimary: noop,
        onDetach: noop,
        onOpenContact: noop,
        related,
        onOpenRelated: noop,
      }),
    );
    expect(html).toContain("Related lists");
    expect(html).toContain("Bosch order");
  });
});

describe("the Open in Zoho link", () => {
  it("passes an http or https address and nothing else", () => {
    expect(zohoLink({ ext_url: "https://crm.zoho.in/crm/org1/tab/Leads/9" })).toBe(
      "https://crm.zoho.in/crm/org1/tab/Leads/9",
    );
    expect(zohoLink({ ext_url: "javascript:alert(1)" })).toBeNull();
    expect(zohoLink({ ext_url: null })).toBeNull();
    expect(zohoLink({})).toBeNull();
    expect(zohoLink(null)).toBeNull();
  });

  it("sits in the record header", () => {
    const src = read("./RecordSheet.tsx");
    expect(src).toContain("Open in Zoho");
    expect(src).toMatch(/zohoLink\(record\)/);
  });
});

// ── The settings page ─────────────────────────────────────────────────────

describe("CRM settings", () => {
  const deal = [status("d1", "Qualified", 0)];
  const pipelines: DealPipeline[] = [
    {
      id: "p1",
      name: "Sales",
      is_default: true,
      position: 0,
      ext_url: null,
      stages: [{ id: "a", name: "Qualified", position: 0, type: "open" }],
    },
    {
      id: "p2",
      name: "Renewals",
      is_default: false,
      position: 1,
      ext_url: "https://crm.zoho.in/p2",
      stages: [{ id: "b", name: "Renew now", position: 0, type: "open" }],
    },
  ];
  const draw = (over: Record<string, unknown> = {}) =>
    renderToStaticMarkup(
      createElement(PipelineSettings, {
        setup: createElement("section", { "aria-label": "Setup" }, "SETUP"),
        access: createElement("section", { "aria-label": "Access" }, "ACCESS"),
        showAdminCards: true,
        dealStatuses: deal,
        leadStatuses: [status("l1", "New", 0)],
        lostReasons: [{ id: "r1", label: "Price", position: 0 }],
        saving: false,
        onSaveStatus: async () => true,
        onRemoveStatus: async () => true,
        onReorderStatuses: async () => undefined,
        onSaveLostReason: async () => true,
        onRemoveLostReason: async () => true,
        onReorderLostReasons: async () => undefined,
        ...over,
      }),
    );

  it("draws the cards in the order of the spec", () => {
    const html = draw({ readOnly: true });
    const order = [
      "SETUP",
      "ACCESS",
      "Pipelines and stages",
      "Lead Status",
      "Lost reasons",
      "Fields",
      "Sync health",
    ].map((word) => html.indexOf(word));
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("draws Sync health in Mirror mode only (CRM-Q9)", () => {
    expect(draw({ readOnly: true })).toContain("Sync health");
    expect(draw()).not.toContain("Sync health");
    expect(draw({ readOnly: true })).not.toContain("Coming soon");
  });

  it("keeps the admin cards from a member", () => {
    const html = draw({ setup: undefined, access: undefined, connection: undefined, showAdminCards: false });
    expect(html).not.toContain("SETUP");
    expect(html).not.toContain("Coming soon");
    expect(html).toContain("Pipelines and stages");
  });

  it("keeps the stage editing of the one implicit pipeline", () => {
    for (const pipelinesProp of [null, [pipelines[0]]]) {
      const html = draw({ pipelines: pipelinesProp });
      expect(html).toContain("<input");
      expect(html).toContain('draggable="true"');
    }
  });

  it("draws one read-only section for each pipeline when there are several", () => {
    const html = draw({ pipelines });
    expect(html).toContain("Sales");
    expect(html).toContain("Renewals");
    expect(html).toContain("Renew now");
    expect(html).toContain("not available yet");
    expect(html).toContain("Open in Zoho");
  });

  it("reads every stage and says where they come from in Mirror mode", () => {
    const html = draw({ readOnly: true, pipelines });
    expect(html).toContain("Stages come from Zoho.");
    expect(html).toContain("Renew now");
    expect(html).toContain("Price");
    // Mirror mode has no editing control in the stage cards.
    expect(html).not.toMatch(/aria-label="(Delete|Remove)/);
    expect(html).not.toContain('draggable="true"');
  });
});

// ── The settings view that did not open (section 18.6) ───────────────────

describe("the error boundary", () => {
  it("shows one sentence and a Reload button", () => {
    const html = renderToStaticMarkup(createElement(CrmFallback, { error: new Error("boom") }));
    expect(html).toContain('role="alert"');
    expect(html).toContain("Something went wrong on this page. Reload to get the newest version.");
    expect(html).toMatch(/<button[^>]*>[\s\S]*Reload<\/button>/);
  });

  it("names an update when the tab holds old script files", () => {
    const stale = Object.assign(new Error("Loading chunk 42 failed."), { name: "ChunkLoadError" });
    expect(boundaryMessage(stale)).toBe(BOUNDARY_COPY.updated);
    expect(boundaryMessage(new Error("boom"))).toBe(BOUNDARY_COPY.failed);
    expect(boundaryMessage(new Error("Failed to fetch dynamically imported module: /x.js"))).toBe(
      BOUNDARY_COPY.updated,
    );
    const html = renderToStaticMarkup(createElement(CrmFallback, { error: stale }));
    expect(html).toContain("Reload to get the newest version.");
  });

  it("turns a throw into state, and uses the shell's own stale-chunk test", () => {
    expect(CrmBoundary.getDerivedStateFromError(new Error("boom")).error?.message).toBe("boom");
    expect(CrmBoundary.getDerivedStateFromError("text").error).toBeInstanceOf(Error);
    expect(read("../lib/boundary.ts")).toMatch(/from "@\/lib\/shell\/chunkReload"/);
  });

  it("wraps the CRM views in the page, keyed by view", () => {
    const page = read("../page.tsx");
    expect(page).toMatch(/<CrmBoundary key=\{boundaryKey\(view, pipelineId\)\}>/);
    expect(page.indexOf("<CrmBoundary")).toBeLessThan(page.indexOf("<PipelineSettings"));
    expect(page.indexOf("<PipelineSettings")).toBeLessThan(page.indexOf("</CrmBoundary>"));
  });
});

describe("the access section reads groups late", () => {
  it("asks for the group list only when the rule is groups", () => {
    expect(wantsGroups("groups")).toBe(true);
    for (const access of ["admins", "everyone", undefined, null, ""]) {
      expect(wantsGroups(access)).toBe(false);
    }
  });

  it("makes the read inside an effect that waits for that answer", () => {
    const src = read("./AccessPanel.tsx");
    // The only call of listGroups is guarded, and the effect depends on the guard.
    expect(src.match(/api\.listGroups\(\)/g)).toHaveLength(1);
    expect(src).toMatch(/const needGroups = !settings\.loading && wantsGroups\(access\);/);
    expect(src).toMatch(/if \(!needGroups\) return;[\s\S]*api\.listGroups\(\)/);
    expect(src).toMatch(/\}, \[needGroups\]\);/);
  });
});

describe("the shell jobs", () => {
  it("keeps /crm?do=new-lead working", () => {
    const page = read("../page.tsx");
    expect(page.match(/<ShellJob\s+id="new-lead"/g)).toHaveLength(2);
    expect(page).toContain("setCreating(\"leads\")");
  });
});

describe("related lists, hardened (review P2)", () => {
  it("says Showing 50 of N when the page is short of the whole", () => {
    const related = {
      ...NO_RELATED,
      deals: Array.from({ length: 50 }, (_, i) => ({ id: `d${i}`, name: `D${i}` })),
      dealsTotal: 73,
    };
    const html = renderToStaticMarkup(
      createElement(RelatedLists, { entity: "contacts", related, onOpen: noop }),
    );
    expect(html).toContain("Showing 50 of 73");
    const whole = { ...related, dealsTotal: 50 };
    expect(
      renderToStaticMarkup(createElement(RelatedLists, { entity: "contacts", related: whole, onOpen: noop })),
    ).not.toContain("Showing");
  });

  it("offers a Retry on a failed read, not an empty section", () => {
    const html = renderToStaticMarkup(
      createElement(RelatedLists, {
        entity: "organizations",
        related: { ...NO_RELATED, failed: true },
        onOpen: noop,
        onRetry: noop,
      }),
    );
    expect(html).toContain("Could not load this list.");
    expect(html).toContain("Retry");
    expect(html).not.toContain("No contacts at this company.");
  });

  it("keys the boundary on view, layout and pipeline", async () => {
    const { boundaryKey } = await import("../lib/boundary");
    expect(boundaryKey({ tab: "deals", layout: "board" }, null)).toBe("deals:board");
    expect(boundaryKey({ tab: "deals", layout: "list" }, "p2")).toBe("deals:list:p2");
    expect(read("../page.tsx")).toMatch(/<CrmBoundary key=\{boundaryKey\(view, pipelineId\)\}>/);
  });

  it("opens the list for an old link that carries a filter", async () => {
    const { parseView } = await import("../lib/urlState");
    for (const q of ["?tab=deals&status=s1", "?tab=deals&sort=amount", "?tab=deals&q=bosch"]) {
      expect(parseView(q).layout).toBe("list");
    }
    expect(parseView("?tab=deals&status=s1&layout=board").layout).toBe("board");
    expect(parseView("?tab=pipeline&q=x").layout).toBe("board");
    expect(parseView("?tab=deals").layout).toBe("board");
  });
});
