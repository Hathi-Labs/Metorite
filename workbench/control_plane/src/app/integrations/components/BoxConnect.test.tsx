// The box UI, drawn (WS-55 EA-19d). The runner has no DOM, so each component is
// rendered to a string. The flow of requests is fenced in `../lib/box.test.ts`.
// The source scans fence what a render cannot see: where the token may live.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AgentConnection } from "../lib/agents";
import { BOX_CONNECT_COMMAND, BOX_OFF_MESSAGE, DAY_MS } from "../lib/box";
import { AgentRow } from "./AgentsView";
import { BoxTokenPanel, ConnectBoxButton, ConnectForm, RevokeAllBoxes } from "./BoxConnect";

const noop = () => undefined;
const NOW = Date.now();
const iso = (offsetMs: number) => new Date(NOW + offsetMs).toISOString();

function agent(over: Partial<AgentConnection> = {}): AgentConnection {
  return {
    id: "c1",
    member_email: "a@x.test",
    client_name: "metorite-home",
    client_origin: "urn:metorite:box",
    scopes: ["projects:read", "projects:write"],
    pinned_projects: [],
    include_personal: false,
    created_at: iso(-10 * DAY_MS),
    last_used_at: iso(-3600_000),
    expires_at: iso(80 * DAY_MS),
    credential: "box",
    last_seen_addr: null,
    addr_changed_at: null,
    ...over,
  };
}

const row = (r: AgentConnection, showMember = false) =>
  renderToStaticMarkup(
    createElement(AgentRow, { row: r, showMember, onRevoke: noop, onRotate: showMember ? undefined : noop }),
  );

describe("a box row", () => {
  it("shows the Box badge, Last used, and Rotate, Rotate now and Revoke", () => {
    const html = row(agent());
    expect(html).toContain('data-testid="box-badge"');
    expect(html).toContain("Last used");
    expect(html).not.toContain("Last token refresh");
    expect(html).toContain(">Rotate<");
    expect(html).toContain(">Rotate now<");
    expect(html).toContain(">Revoke<");
  });

  it("is headed by the box name, not by an origin", () => {
    const html = row(agent());
    expect(html).toContain("metorite-home");
    expect(html).not.toContain("urn:metorite:box");
    expect(html).not.toContain("It calls itself");
  });

  it("has no Change access button (spec question 2)", () => {
    expect(row(agent())).not.toContain("Change access");
  });

  it("colours the expiry by the shared vocabulary", () => {
    expect(row(agent())).not.toContain("data-expiry-color");
    const amber = row(agent({ expires_at: iso(10 * DAY_MS) }));
    expect(amber).toContain('data-expiry-color="amber"');
    expect(amber).toContain("Expires in 10 days");
    const red = row(agent({ expires_at: iso(2 * DAY_MS) }));
    expect(red).toContain('data-expiry-color="red"');
    expect(amber).not.toBe(red);
  });

  it("shows the new-address mark inside 24 hours, and not after", () => {
    const fresh = row(agent({ last_seen_addr: "203.0.113.7", addr_changed_at: iso(-3600_000) }));
    expect(fresh).toContain('data-testid="box-new-address"');
    expect(fresh).toContain("203.0.113.7");
    expect(fresh).toContain("&quot;Rotate now&quot; or Revoke if this was not your box");
    const old = row(agent({ last_seen_addr: "203.0.113.7", addr_changed_at: iso(-2 * DAY_MS) }));
    expect(old).not.toContain("box-new-address");
  });

  it("offers no Rotate on another member's row", () => {
    const html = row(agent(), true);
    expect(html).not.toContain(">Rotate<");
    expect(html).toContain(">Revoke<");
  });
});

describe("an OAuth row", () => {
  it("shows nothing new", () => {
    const html = row(agent({ credential: "oauth", client_origin: "https://claude.ai" }));
    expect(html).not.toContain("box-badge");
    expect(html).not.toContain("Rotate");
    expect(html).not.toContain("box-new-address");
    expect(html).toContain("Last token refresh");
  });
});

describe("the dialog", () => {
  it("opens on a button, and holds no form while closed", () => {
    const html = renderToStaticMarkup(createElement(ConnectBoxButton, { onMade: noop }));
    expect(html).toContain("Connect a box");
    expect(html).not.toContain("box-form");
    expect(html).not.toContain("mbt_");
  });

  it("draws the form: name, scope switches, days 90, personal off", () => {
    const html = renderToStaticMarkup(createElement(ConnectForm, { onClose: noop, onMade: noop }));
    expect(html).toContain("Name of the box");
    expect(html).toContain("Read your projects and tasks");
    expect(html).toContain("Change your projects and tasks");
    expect(html).toContain('value="90"');
    expect(html).toContain("Personal projects");
    expect(html).not.toContain("box-name-error");
    expect(html).not.toContain(BOX_OFF_MESSAGE);
  });

  it("draws the token once with Copy, the warning and the command", () => {
    const token = "mbt_" + "B".repeat(43);
    const html = renderToStaticMarkup(
      createElement(BoxTokenPanel, {
        issued: { token, connection_id: "c1", name: "home", scopes: [], expires_at: iso(DAY_MS) },
        onDone: noop,
      }),
    );
    expect(html).toContain(token);
    expect(html).toContain("readOnly");
    expect(html).toContain("Copy the token");
    expect(html).toContain("It cannot be shown again");
    expect(html).toContain(BOX_CONNECT_COMMAND);
    expect(html).toContain("hidden prompt");
    // The token sits in the field only, never in the command or a link.
    expect(html.split(token).length - 1).toBe(1);
  });
});

describe("revoke all", () => {
  it("draws the admin button", () => {
    expect(renderToStaticMarkup(createElement(RevokeAllBoxes, { onDone: noop }))).toContain("Revoke all box tokens");
  });
});

describe("where the token may live", () => {
  const dir = join(__dirname, "..");
  const sources = [join(dir, "components/BoxConnect.tsx"), join(dir, "lib/box.ts")].map((p) => readFileSync(p, "utf-8"));

  it("is not stored, logged or put in a URL", () => {
    for (const src of sources) {
      const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
      expect(code).not.toMatch(/localStorage|sessionStorage|console\.|document\.cookie|useCachedResource|dataCache|router\.(push|replace)/);
    }
  });

  it("is cleared on close: the form mounts only while the dialog is open", () => {
    expect(sources[0]).toMatch(/\{open \? \(\s*<div className="p-4">\s*<ConnectForm/);
  });
});

describe("the wiring of Revoke all in the view", () => {
  it("passes the real admin flag, never a literal", () => {
    const src = readFileSync(new URL("./AgentsView.tsx", import.meta.url), "utf8");
    expect(src).toContain("showRevokeAll(canManageOrg, org.data)");
    expect(src).not.toMatch(/showRevokeAll\(\s*true/);
  });
});
