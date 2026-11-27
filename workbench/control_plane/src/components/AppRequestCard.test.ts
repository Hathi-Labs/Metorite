// AppRequestCard, the one card of an app request (WS-MCP MCP-P1b-1).
//
// Spec: `external_agent_access.md` §19.5 ("The card") and D8 of the MCP-P1b
// audit. The rules held here:
//
// - no inner HTML and no Markdown in the card (a source scan);
// - the mark "Text from the agent. Check it." sits over the values;
// - each value shows whole, as text, and an HTML tag in a value stays text;
// - the server's frame: the title, the client, the class line, the expiry;
// - Approve and Decline call `onDecide` with their decision, only once armed,
//   and only while the request waits and is live.
//
// The file is `.test.ts`, not `.test.tsx`: `vitest.config.ts` includes
// `src/**/*.test.ts` only. It draws the card with `createElement`.
//
// Mutations, each one red here:
// - draw a value with `dangerouslySetInnerHTML`: the source scan and the
//   HTML case fail;
// - drop the mark: the mark case fails;
// - cut a long value: the whole-value case fails;
// - swap the decisions of the two buttons: the click case fails;
// - drop the `armed` guard: the arm case fails.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import AppRequestCard, { AppRequestCardView } from "@/components/AppRequestCard";
import {
  AGENT_TEXT_MARK,
  askLine,
  canDecide,
  clientHost,
  expiryText,
  payloadRows,
  type AppRequest,
} from "@/lib/appRequest";

const NOW = Date.parse("2026-11-04T10:00:00Z");
const LATER = "2026-11-05T10:00:00+00:00";
const SRC = fileURLToPath(new URL("..", import.meta.url));

function request(extra: Partial<AppRequest> = {}): AppRequest {
  return {
    request_id: "a60e8cc6-81e5-4e3a-ac8e-2ca429703b30",
    run_id: "b60e8cc6-81e5-4e3a-ac8e-2ca429703b30",
    state: "awaiting",
    decision: null,
    source: "mcp",
    app: "knowledge",
    subject: null,
    op: "edit_page",
    class: "B",
    client_via: "mcp:claude.ai:claude-code",
    card: {
      app_name: "Knowledge",
      title: "Edit one page.",
      class_line: "You can undo this in Knowledge.",
      client: "Claude Code",
    },
    call: {
      method: "PATCH",
      template: "/knowledge/pages/{page_id}",
      path: "/knowledge/pages/0b5c6c1e-1111-4222-8333-444444444444",
      query: [],
      body: { title: "Shift handover", body: "Line one.\nLine two." },
      if_match: "7",
    },
    expires_at: LATER,
    created_at: "2026-11-04T09:00:00+00:00",
    decided_at: null,
    ...extra,
  };
}

function html(props: Partial<Parameters<typeof AppRequestCardView>[0]> = {}): string {
  return renderToStaticMarkup(
    createElement(AppRequestCardView, {
      request: request(),
      onDecide: () => {},
      now: NOW,
      armed: true,
      ...props,
    }),
  );
}

/** Every element of a tree that a component returned, with no render. */
function elements(node: ReactNode): ReactElement[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const props = node.props as { children?: ReactNode };
  return [node, ...elements(props.children)];
}

function button(tree: ReactNode, mark: string): { onClick: () => void; disabled?: boolean } {
  const found = elements(tree).find((el) => (el.props as Record<string, unknown>)[mark] !== undefined);
  expect(found, mark).toBeTruthy();
  return found!.props as { onClick: () => void; disabled?: boolean };
}

describe("the card draws no HTML that the agent wrote", () => {
  it("has no inner HTML and no Markdown renderer in its source", () => {
    const src = readFileSync(`${SRC}/components/AppRequestCard.tsx`, "utf8");
    const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
    expect(code).not.toMatch(/dangerouslySetInnerHTML|innerHTML|Markdown|FencedText/);
    const lib = readFileSync(`${SRC}/lib/appRequest.ts`, "utf8");
    expect(lib).not.toMatch(/innerHTML|Markdown/);
  });

  it("draws an HTML tag in a value as text", () => {
    const out = html({
      request: request({ call: { method: "POST", path: "/x", body: { title: "<img src=x onerror=alert(1)>" } } }),
    });
    expect(out).not.toContain("<img");
    expect(out).toContain("&lt;img src=x onerror=alert(1)&gt;");
  });
});

describe("the server's frame and the agent's values", () => {
  it("puts the mark over the values, and each value under it", () => {
    const out = html();
    expect(out).toContain(AGENT_TEXT_MARK);
    const payload = out.slice(out.indexOf("data-app-request-payload"));
    expect(payload.indexOf(AGENT_TEXT_MARK)).toBeLessThan(payload.indexOf("Shift handover"));
    expect(payload).toContain("Line one.\nLine two.");
    expect(payload).toContain("PATCH /knowledge/pages/0b5c6c1e-1111-4222-8333-444444444444");
  });

  it("shows the title, the client and its host, the class line and the expiry", () => {
    const out = html();
    expect(out).toContain("Edit one page.");
    expect(out).toContain("Claude Code · claude.ai asks to change Knowledge.");
    expect(out).toContain("You can undo this in Knowledge.");
    expect(out).toContain(expiryText(LATER, NOW));
  });

  it("marks class C as hard to undo", () => {
    const out = html({ request: request({ class: "C", card: { ...request().card, class_line: "This is hard to undo." } }) });
    expect(out).toContain('data-app-request-class="C"');
    expect(out).toContain("This is hard to undo.");
    expect(out).toContain("text-warning");
  });

  it("shows a long value whole", () => {
    const long = "word ".repeat(4000).trim();
    const out = html({ request: request({ call: { method: "POST", path: "/x", body: { body: long } } }) });
    expect(out).toContain(long);
  });

  it("lists the rows of a call: the target, the query, the version, then the body", () => {
    const rows = payloadRows({
      method: "POST",
      path: "/a/b",
      query: [["notify", "false"]],
      if_match: "3",
      body: { title: "T", tags: ["x", "y"], due_at: null },
    });
    expect(rows.map((r) => r.key)).toEqual([
      "target",
      "query:notify",
      "if_match",
      "body:title",
      "body:tags",
      "body:due_at",
    ]);
    expect(rows.find((r) => r.key === "body:tags")?.value).toBe('[\n  "x",\n  "y"\n]');
    expect(rows.find((r) => r.key === "body:due_at")?.value).toBe("None");
  });

  // Review P3-3. A field of the agent named `target`, `version` or
  // `if_match` must not copy the key or the label of a row of the frame.
  it("keeps the keys and the labels of the Target and Version rows for the frame", () => {
    const rows = payloadRows({
      method: "PATCH",
      path: "/a/b",
      query: [["target", "q"]],
      if_match: "3",
      body: { target: "PATCH /something/harmless", version: "9", if_match: "x", title: "ok" },
    });
    const keys = rows.map((r) => r.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys.slice(0, 3)).toEqual(["target", "query:target", "if_match"]);
    const frame = rows.filter((r) => r.label === "Target" || r.label === "Version");
    expect(frame.map((r) => r.key)).toEqual(["target", "if_match"]);
    expect(rows.find((r) => r.key === "body:target")?.label).toBe('Field "target"');
    expect(rows.find((r) => r.key === "body:version")?.label).toBe('Field "version"');
    expect(rows.find((r) => r.key === "query:target")?.label).toBe('Field "target"');
  });

  // A noted item of the review. A right-to-left override in agent text can
  // make "invoice<RLO>fdp.exe" read as "invoiceexe.pdf".
  it("drops each bidi control from a value and from a field name", () => {
    const rows = payloadRows({
      method: "POST",
      path: "/a",
      body: { ["na\u2066me"]: "invoice\u202Efdp.exe", note: "a\u2067b\u2069\u200Ec\u061C" },
    });
    expect(rows.find((r) => r.key.endsWith("me"))?.value).toBe("invoicefdp.exe");
    expect(rows.find((r) => r.key.endsWith("me"))?.label).toBe("Name");
    expect(rows.find((r) => r.key === "body:note")?.value).toBe("abc");
    const out = html({
      request: request({ call: { method: "POST", path: "/x\u202E", body: { title: "invoice\u202Efdp.exe" } } }),
    });
    expect(out).toContain("invoicefdp.exe");
    expect(out).not.toMatch(/[\u061C\u200E\u200F\u202A-\u202E\u2066-\u2069]/);
  });

  // Re-review P1. An agent's body may hold a key that names a member of
  // `Object.prototype`. JSON.parse makes each one an own key, `__proto__`
  // too. Before the fix the label lookup threw, the error page replaced
  // Integrations, and the member could not decline the request.
  it("draws a row for each key that names a member of Object.prototype", () => {
    const keys = ["constructor", "toString", "valueOf", "hasOwnProperty", "__proto__"];
    const body = JSON.parse(
      '{"name":"x","constructor":"c1","toString":"t1","valueOf":"v1","hasOwnProperty":"h1","__proto__":"p1"}',
    ) as Record<string, unknown>;
    expect(Object.keys(body)).toEqual(["name", ...keys]);
    const rows = payloadRows({ method: "POST", path: "/a", body });
    for (const key of keys) {
      const row = rows.find((r) => r.key === `body:${key}`);
      expect(row, key).toBeTruthy();
      expect(typeof row?.label).toBe("string");
    }
    const out = html({ request: request({ call: { method: "POST", path: "/a", body } }) });
    for (const value of ["c1", "t1", "v1", "h1", "p1"]) expect(out).toContain(value);
    expect(out).toContain("data-app-request-decline");
  });

  it("reads the host of a stamp as the gateway does", () => {
    expect(clientHost("mcp:claude.ai:claude-code")).toBe("claude.ai");
    expect(clientHost("mcp:claude.ai")).toBe("claude.ai");
    expect(clientHost("studio:approve-order")).toBe("");
    expect(askLine({ ...request(), client_via: "studio:x", card: { client: "Studio", app_name: "Orders" } }))
      .toBe("Studio asks to change Orders.");
  });
});

describe("Approve and Decline", () => {
  it("each button sends its own decision", () => {
    const onDecide = vi.fn();
    const tree = AppRequestCardView({ request: request(), onDecide, now: NOW, armed: true });
    button(tree, "data-app-request-approve").onClick();
    button(tree, "data-app-request-decline").onClick();
    expect(onDecide.mock.calls).toEqual([["approve"], ["decline"]]);
  });

  it("a new card ignores a click until it is armed", () => {
    const onDecide = vi.fn();
    const tree = AppRequestCardView({ request: request(), onDecide, now: NOW, armed: false });
    button(tree, "data-app-request-approve").onClick();
    expect(onDecide).not.toHaveBeenCalled();
  });

  it("a decision on its way disables both buttons and names its verb", () => {
    const onDecide = vi.fn();
    const tree = AppRequestCardView({ request: request(), onDecide, now: NOW, armed: true, busy: "approve" });
    expect(button(tree, "data-app-request-decline").disabled).toBe(true);
    button(tree, "data-app-request-decline").onClick();
    expect(onDecide).not.toHaveBeenCalled();
    expect(html({ busy: "approve" })).toContain("Approving…");
  });

  it("an error shows next to the buttons", () => {
    const out = html({ error: "This request already has an answer." });
    expect(out).toContain('role="alert"');
    expect(out).toContain("This request already has an answer.");
  });

  it("a request that expired or has an answer offers no button", () => {
    for (const extra of [
      { expires_at: "2026-11-04T09:59:00+00:00" },
      { state: "approved" as const },
      { state: "declined" as const },
      { state: "done" as const },
    ]) {
      const out = html({ request: request(extra) });
      expect(out).not.toContain("data-app-request-approve");
      expect(out).not.toContain("data-app-request-decline");
    }
    expect(html({ request: request({ expires_at: "2026-11-04T09:59:00+00:00" }) })).toContain("Expired");
    expect(canDecide({ state: "awaiting", expires_at: "not a time" }, NOW)).toBe(false);
  });

  it("the stateful card draws the same markup, unarmed at first", () => {
    const out = renderToStaticMarkup(createElement(AppRequestCard, { request: request(), onDecide: () => {}, now: NOW }));
    expect(out).toContain("data-app-request-approve");
    expect(out).toContain('aria-disabled="true"');
  });
});
