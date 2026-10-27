// The CRM's first-open cards, banners and mirror marks, drawn (CRM-U5, CRM-Z7).
//
// The runner has no DOM, so each component is rendered to a string.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { BoardLane } from "../lib/board";
import { modeLine, type ConnectionInfo, type SyncStatus } from "../lib/connect";
import AccessPanel from "./AccessPanel";
import PipelineSettings from "./PipelineSettings";
import FieldsPanel from "./FieldsPanel";
import FirstOpen from "./FirstOpen";
import KanbanBoard from "./KanbanBoard";
import { ImportProgress, StalledBanner } from "./MirrorBanner";
import Timeline from "./Timeline";

const noop = () => undefined;

const conn: ConnectionInfo = {
  connected: true,
  provider: "zoho",
  owning_app: "crm",
  status: "connected",
  location: "in",
  scopes: [],
  connected_by: "dana@x.test",
  last_sync_at: null,
  last_error: null,
  mode: "mirror",
};

describe("the first open", () => {
  const draw = (phase: Parameters<typeof FirstOpen>[0]["phase"]) =>
    renderToStaticMarkup(
      createElement(FirstOpen, { phase, busy: null, onNative: noop, onConnectZoho: noop }),
    );

  it("shows an admin two cards, with Zoho live and three coming soon", () => {
    const html = draw("choose");
    expect(html).toContain("Use Metorite as your CRM");
    expect(html).toContain("Connect your CRM");
    for (const name of ["Zoho CRM", "HubSpot", "Salesforce", "Pipedrive"]) {
      expect(html).toContain(name);
    }
    expect(html.match(/Coming soon/g)).toHaveLength(3);
    // Zoho's Connect is the one enabled Connect button.
    const connects = [...html.matchAll(/<button[^>]*>[\s\S]*?<\/button>/g)]
      .map((m) => m[0])
      .filter((b) => b.replace(/<[^>]*>/g, "").trim() === "Connect");
    expect(connects).toHaveLength(4);
    expect(connects.filter((b) => !/ disabled=/.test(b))).toHaveLength(1);
  });

  it("shows only the Native card when the mirror is off", () => {
    const html = draw("native-only");
    expect(html).toContain("Use Metorite as your CRM");
    expect(html).not.toContain("Connect your CRM");
    expect(html).not.toContain("HubSpot");
  });

  it("tells a member to wait for an admin", () => {
    const html = draw("wait-admin");
    expect(html).toContain("An admin must set up the CRM first.");
    expect(html).not.toContain("Use Metorite as your CRM");
  });

  it("keeps the app's own title bar over the cards", () => {
    expect(draw("choose")).toMatch(/<h1\b[^>]*>CRM<\/h1>/);
  });
});

describe("the banners", () => {
  it("sends an admin to the setup hub (CRM-X0)", () => {
    const html = renderToStaticMarkup(
      createElement(StalledBanner, { isAdmin: true, status: "needs_reconnect", onOpenSetup: noop }),
    );
    expect(html).toContain("Open CRM setup");
    expect(html).toContain('role="alert"');
  });
  it("tells a member to ask an admin", () => {
    const html = renderToStaticMarkup(
      createElement(StalledBanner, { isAdmin: false, status: "needs_reconnect", onOpenSetup: noop }),
    );
    expect(html).toContain("Ask an admin to connect Zoho again");
    expect(html).not.toContain("<button");
  });
  it("draws the import as a bar when an estimate exists", () => {
    const status: SyncStatus = {
      state: "running",
      kind: "import",
      started_at: null,
      finished_at: null,
      last_success_at: null,
      counts: { contacts: 100 },
      estimate: { contacts: 400 },
      error: null,
      next_run_at: null,
    };
    const html = renderToStaticMarkup(createElement(ImportProgress, { status }));
    expect(html).toContain('role="progressbar"');
    expect(html).toContain('aria-valuenow="25"');
    expect(html).toContain("Contacts 100 of 400");
  });
});

describe("the locked fields", () => {
  const draw = (record: Record<string, unknown>, part: "overview" | "fields" = "overview") =>
    renderToStaticMarkup(
      createElement(FieldsPanel, {
        part,
        entity: "organizations",
        record,
        statuses: [],
        dealContacts: [],
        saving: false,
        onPatch: noop,
        onConvert: noop,
        onSetPrimary: noop,
        onDetach: noop,
        onOpenContact: noop,
      }),
    );

  it("locks a record that Zoho owns, with the tooltip", () => {
    const html = draw({ id: "1", name: "Acme", ext_source: "zoho" });
    expect(html).toContain("Synced from Zoho. Change it in Zoho.");
    expect(html).toContain("Acme");
    // No field is a button that starts an edit.
    expect(html).not.toMatch(/<button[^>]*>\s*Acme/);
  });

  it("leaves a native record editable", () => {
    const html = draw({ id: "1", name: "Acme", ext_source: null });
    expect(html).not.toContain("Synced from Zoho");
    expect(html).toMatch(/<button[^>]*>Acme<\/button>/);
  });

  it("shows custom fields as read-only rows", () => {
    const html = draw({ id: "1", name: "Acme", ext_source: "zoho", custom: { Lead_Source: "Web" } }, "fields");
    expect(html).toContain("Lead Source");
    expect(html).toContain("Web");
  });
});

describe("the pipeline settings in Mirror mode", () => {
  const draw = (readOnly: boolean) =>
    renderToStaticMarkup(
      createElement(PipelineSettings, {
        readOnly,
        dealStatuses: [
          { id: "s1", name: "Qualified", color: "blue", position: 0, type: "open", is_default: true },
        ],
        leadStatuses: [],
        lostReasons: [],
        saving: false,
        canPullStages: true,
        onSaveStatus: async () => true,
        onRemoveStatus: async () => true,
        onReorderStatuses: async () => undefined,
        onSaveLostReason: async () => true,
        onRemoveLostReason: async () => true,
        onReorderLostReasons: async () => undefined,
        onPullStages: async () => null,
      }),
    );
  it("says where the stages come from, and shuts every control", () => {
    const html = draw(true);
    expect(html).toContain("Stages come from Zoho.");
    // Plain text rows: no input, no drag handle, no delete button.
    expect(html).toContain("Qualified");
    expect(html).not.toContain("<input");
    expect(html).not.toContain("draggable");
    expect(html).not.toMatch(/aria-label="(Delete|Remove)/);
    expect(html).not.toContain("Repair from Zoho");
  });
  it("stays editable in Native mode", () => {
    const html = draw(false);
    expect(html).not.toContain("Stages come from Zoho.");
    expect(html).toContain("Qualified");
    expect(html).not.toContain("Repair from Zoho");
  });
});

describe("the board", () => {
  const lanes = (ext: string | null): BoardLane[] =>
    [
      {
        status: { id: "s1", name: "Open", color: "blue", position: 0, type: "open", is_default: true },
        rows: [{ id: "d1", name: "Big deal", ext_source: ext }],
        count: 1,
        amount: 0,
        weighted: 0,
      },
    ] as unknown as BoardLane[];
  const draw = (ext: string | null, onCreate?: () => void) =>
    renderToStaticMarkup(
      createElement(KanbanBoard, { lanes: lanes(ext), loading: false, onMove: noop, onOpen: noop, onCreate }),
    );

  it("drops the New deal button when no create handler is given", () => {
    expect(draw(null)).not.toContain("New deal");
    expect(draw(null, noop)).toContain("New deal");
  });
  it("does not let a mirrored deal be dragged", () => {
    expect(draw("zoho")).toContain('draggable="false"');
    expect(draw(null)).toContain('draggable="true"');
  });
});

describe("the timeline", () => {
  const entries = [
    {
      kind: "activity",
      origin: "own",
      activity: { id: "a1", type: "note", body: "mine", ext_source: null },
    },
    {
      kind: "activity",
      origin: "own",
      activity: { id: "a2", type: "note", body: "theirs", ext_source: "zoho" },
    },
  ] as never;
  const draw = (parentMirrored: boolean) =>
    renderToStaticMarkup(
      createElement(Timeline, { entries, onLog: noop, onToggleTask: noop, saving: false, parentMirrored }),
    );

  it("badges only the Metorite-only activity, only under a mirrored record", () => {
    expect(draw(true).match(/Only in Metorite/g)).toHaveLength(1);
    expect(draw(false)).not.toContain("Only in Metorite");
  });
});

describe("the page", () => {
  const page = readFileSync(new URL("../page.tsx", import.meta.url), "utf8");
  it("hides the New verb and the board's create in Mirror mode", () => {
    expect(page).toMatch(/mayCreate && view\.tab !== "settings"/);
    expect(page).toMatch(/onCreate=\{mayCreate \?/);
  });
  it("loads nothing for the pipeline until the gate says ready", () => {
    expect(page).toMatch(/if \(phase !== "ready"\)/);
  });
  it("keeps every app element out of the shell bar", () => {
    expect(page).toContain("<AppTopBar");
  });
});

describe("who may open the CRM (CRM-L2)", () => {
  it("tells a member with no access, in an empty state", () => {
    const html = renderToStaticMarkup(
      createElement(FirstOpen, { phase: "no-access", busy: null, onNative: noop, onConnectZoho: noop }),
    );
    expect(html).toContain("The CRM is not open to you yet");
    expect(html).toContain("An admin chooses who can use the CRM in CRM settings.");
    expect(html).not.toContain("Use Metorite");
  });

  it("tells an admin that only admins can open it, and shows the control", () => {
    const html = renderToStaticMarkup(
      createElement(AccessPanel, { access: undefined, groups: [], allGroups: [], saving: false, onChange: noop }),
    );
    expect(html).toContain("Only admins can open the CRM now.");
    expect(html).toContain('id="crm-access"');
  });

  it("drops the hint once an admin chose everyone, and lists groups for the group choice", () => {
    const everyone = renderToStaticMarkup(
      createElement(AccessPanel, { access: "everyone", groups: [], allGroups: [], saving: false, onChange: noop }),
    );
    expect(everyone).not.toContain("Only admins can open the CRM now.");
    const groups = renderToStaticMarkup(
      createElement(AccessPanel, {
        access: "groups",
        groups: ["group:sales"],
        allGroups: [{ slug: "sales", display_name: "Sales" }, { slug: "ops" }],
        saving: false,
        onChange: noop,
      }),
    );
    expect(groups).toContain("Sales");
    expect(groups).toContain("ops");
    expect(groups).toContain("checked");
  });
});

describe("the other refusals and the reconnect marks (CRM-L2)", () => {
  const draw = (phase: Parameters<typeof FirstOpen>[0]["phase"]) =>
    renderToStaticMarkup(
      createElement(FirstOpen, { phase, busy: null, onNative: noop, onConnectZoho: noop }),
    );
  it("tells a member without the grant to ask for it", () => {
    const html = draw("no-feature");
    expect(html).toContain("You do not have access to the CRM");
    expect(html).toContain("Ask an admin for the CRM permission.");
    expect(html).not.toContain("not open to you yet");
  });
  it("tells an org with the CRM off", () => {
    expect(draw("off")).toContain("The CRM is not turned on for your organization yet.");
  });
  it("draws Open CRM setup as a primary button", () => {
    const html = renderToStaticMarkup(
      createElement(StalledBanner, { isAdmin: true, status: "needs_reconnect", onOpenSetup: noop }),
    );
    expect(html).toContain("Open CRM setup");
    expect(html).toContain("bg-primary");
  });
  it("names the disconnected state in the title bar line", () => {
    const sync = { state: "idle", last_success_at: "2026-10-11T11:53:00Z" } as unknown as SyncStatus;
    const now = Date.parse("2026-10-11T12:00:00Z");
    const line = modeLine({ mode: "mirror", connection: { ...conn, status: "needs_reconnect" }, sync, nowMs: now });
    expect(line).toBe("Zoho disconnected · last synced 7m ago");
    expect(modeLine({ mode: "mirror", connection: conn, sync, nowMs: now })).toContain("Synced from Zoho");
  });
});
