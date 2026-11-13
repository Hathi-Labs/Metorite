/**
 * The approval card of a Studio record change, drawn in the chat
 * (STU-B2c-2c, `project-docs/specs/ai_studio.md` §14 row B2c-2c).
 *
 * `vitest.config.ts` runs in `node` with no DOM, so this file draws the card
 * with `renderToStaticMarkup`, over a request that the one read cache already
 * holds. A fresh mount reads the server's state, as a reload of the chat
 * does. The rules of a press live in `lib/studio/studioCardState.ts` and its
 * test. The source pins below hold each wiring line that a pure test cannot
 * reach, and each one fails when its line goes.
 *
 * Mutations, each one red here (2026-11-08):
 * - draw the buttons for a decided request: "a decided or expired request
 *   shows no buttons";
 * - call `openAssistant` with `typed`: "the card names no new event";
 * - drop the mount in `MessageBubble`: "MessageBubble mounts the card".
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { requestKey } from "@/app/integrations/lib/appRequests";
import type { ToolEvent } from "@/components/MarkdownMessage";
import type { AppRequest } from "@/lib/appRequest";
import { clearAll, put } from "@/lib/dataCache";
import { __forgetCardsForTests, NOT_SAVED_YET, STUDIO_CARD_TOOLS, TELL_LABEL } from "@/lib/studio/studioCardState";

import StudioToolCards from "./StudioToolCards";

const RID = "5b1e3c2a-7d4f-4a6b-9c8d-0e1f2a3b4c5d";
const PKG = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(`${SRC}/${rel}`, "utf8").replace(/\r\n/g, "\n");
/** Code and markup only: no comments. */
const code = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

function askEvent(tool: string, id = RID): ToolEvent {
  return {
    id: `call-${tool}`,
    name: tool,
    args: { package_id: PKG, object: "visit" },
    result: JSON.stringify({
      ok: false, errors: [], diff: [], saved: false, status: 202, state: "awaiting", request_id: id,
      expires_at: "2099-01-01T00:00:00+00:00", note: "This change is not saved.",
    }),
    status: "done",
  };
}

const BODIES: Record<string, { op: string; title: string; cls: "B" | "C"; body: unknown }> = {
  studio_record_create: { op: "add", title: "Add a record to visit.", cls: "B", body: { data: { site: "Plant 4" } } },
  studio_record_update: {
    op: "change", title: "Change a record of visit.", cls: "B",
    body: { id: "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f", data: { notes: "Late" } },
  },
  studio_record_delete: {
    op: "remove", title: "Delete a record of visit.", cls: "C", body: { id: "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f" },
  },
};

function request(tool: string, extra: Partial<AppRequest> = {}): AppRequest {
  const b = BODIES[tool];
  return {
    request_id: RID,
    run_id: "1b1e3c2a-7d4f-4a6b-9c8d-0e1f2a3b4c5d",
    state: "awaiting",
    decision: null,
    source: "studio",
    app: "studio",
    subject: PKG,
    op: b.op,
    class: b.cls,
    client_via: "studio:door",
    card: { app_name: "Visits", title: b.title, client: "Studio" },
    call: { method: "POST", template: "/studio/apps/{app_id}/actions/{action}",
      path: `/studio/apps/${PKG}/actions/${b.op}`, query: [["uses", `object:visit.${b.op}`]], body: b.body },
    expires_at: "2099-01-01T00:00:00+00:00",
    created_at: "2026-11-08T09:00:00+00:00",
    decided_at: null,
    ...extra,
  };
}

function draw(events: ToolEvent[]): string {
  return renderToStaticMarkup(createElement(StudioToolCards, { toolEvents: events, sessionId: "chat-1" }));
}

afterEach(() => {
  clearAll();
  __forgetCardsForTests();
});

describe("the card draws from an ask of each of the three write tools", () => {
  for (const tool of STUDIO_CARD_TOOLS) {
    it(tool, () => {
      put(requestKey(RID), request(tool));
      const html = draw([askEvent(tool)]);
      expect(html).toContain(`data-app-request-card="${RID}"`);
      expect(html).toContain(BODIES[tool].title);
      expect(html).toContain("Studio asks to change Visits.");
      expect(html).toContain("data-app-request-approve");
      expect(html).toContain("data-app-request-decline");
    });
  }

  it("a turn with no ask draws nothing, and nor does a request that is not the member's", () => {
    expect(draw([])).toBe("");
    const resend = { ...askEvent("studio_record_create"), result: JSON.stringify({ ok: true, saved: true, status: 200, request_id: RID }) };
    expect(draw([resend])).toBe("");
    // 404: the read gives null. The card draws nothing.
    put(requestKey(RID), null);
    expect(draw([askEvent("studio_record_create")])).toBe("");
  });

  it("before the read lands, it keeps the space of the card", () => {
    const html = draw([askEvent("studio_record_create")]);
    expect(html).toContain('aria-hidden="true"');
    // About the height of the card (fix round 1), so the turns below do not jump.
    expect(html).toContain("h-80");
  });
});

describe("a decided or expired request shows no buttons, also after a reload", () => {
  const cases: Array<[string, Partial<AppRequest>, string]> = [
    ["approved", { state: "approved", decision: "approve" }, "Approved"],
    ["declined", { state: "declined", decision: "decline" }, "Declined"],
    ["done", { state: "done", decision: "approve" }, "Done"],
    ["expired", { state: "expired" }, "Expired"],
    ["past its expiry", { state: "awaiting", expires_at: "2020-01-01T00:00:00+00:00" }, "Expired"],
  ];
  for (const [name, extra, word] of cases) {
    it(name, () => {
      // A fresh mount: the page reloaded, and the card holds no local state.
      put(requestKey(RID), request("studio_record_create", extra));
      const html = draw([askEvent("studio_record_create")]);
      expect(html).toContain(`data-app-request-card="${RID}"`);
      expect(html).not.toContain("data-app-request-approve");
      expect(html).not.toContain("data-app-request-decline");
      expect(html).toContain(`>${word}<`);
      if (name === "approved") {
        // Fix round 1: an approval that never ran says so, with one control.
        expect(html).toContain("data-app-request-act");
        expect(html).toContain(TELL_LABEL);
        expect(html).toContain(NOT_SAVED_YET);
      } else {
        // No follow-up line and no control: this mount sent nothing.
        expect(html).not.toContain("data-app-request-note");
        expect(html).not.toContain("data-app-request-act");
      }
    });
  }
});

describe("the wiring", () => {
  const card = read("components/studio/StudioToolCards.tsx");
  const live = code(card);

  it("the card file names no request_confirmation, no new event and no decision tool", () => {
    expect(card).not.toMatch(/request_confirmation|requestConfirmation/);
    expect(live).not.toMatch(/CustomEvent|dispatchEvent|addEventListener|generative_ui|TOOL_CALL|respond-input/);
    expect(live).not.toMatch(/\bfetch\(/);
    expect(live).not.toMatch(/\/decision/);
    expect(live).not.toMatch(/typed/);
  });

  it("a press goes through the one rule, with the card's state read at once", () => {
    expect(live).toMatch(/void pressDecision\(requestId, decision, \(\) => localRef\.current, set, \{/);
    expect(live).toMatch(/decide: decideRequest,/);
    expect(live).toMatch(/localRef\.current = next;\s*rememberCard\(requestId, next\);\s*setLocal\(next\);/);
  });

  it("the follow-up goes into the same chat through the shell's one assistant", () => {
    expect(live).toContain(
      "const tell = (text: string) => openAssistant({ text, joinOnly: true, ...(sessionId ? { sessionId } : {}) });");
    expect(live).toMatch(/decide: decideRequest,\s*tell,/);
  });

  it("Tell the assistant goes through the one guard, with the same join-only send", () => {
    expect(live).toContain("const act = tellShows(shown, local, outcome)");
    expect(live).toContain("onClick: () => void pressTell(requestId, () => localRef.current, set, tell) }");
    expect(live).toContain("note={cardNote(shown, local, outcome)}");
    expect(live).toContain("act={act}");
  });

  it("each answer reads the request again, and a refusal does too", () => {
    expect(live).toContain("}).finally(() => invalidate(APP_REQUESTS_KEY));");
  });

  it("the card reads the request through the one cache, and draws the server's state with the local decision", () => {
    expect(live).toContain("useCachedResource(requestKey(requestId), () => readRequest(requestId))");
    expect(live).toContain("const shown = shownRequest(read.data, local);");

    expect(live).toContain("const outcome = useAskOutcome(local.askSeq);");
  });

  it("every hook runs before the first return", () => {
    const body = live.slice(live.indexOf("export function StudioRequestCard"), live.indexOf("export default function"));
    const firstReturn = body.indexOf("return");
    const hooks = [...body.matchAll(/\buse[A-Z]\w*(?:<[^>]*>)?\(/g)].map((m) => m.index ?? 0);
    expect(hooks.length).toBeGreaterThan(3);
    expect(Math.max(...hooks)).toBeLessThan(firstReturn);
  });

  it("MessageBubble mounts the card beside the other tool cards", () => {
    const bubble = read("components/MessageBubble.tsx");
    expect(bubble).toContain('import StudioToolCards from "@/components/studio/StudioToolCards";');
    expect(bubble).toContain("<StudioToolCards toolEvents={dedupedToolEvents} sessionId={sessionId} />");
  });

  it("the client reads one request through the BFF of MCP-P1b-1, and a 404 is no card", () => {
    const client = read("app/integrations/lib/appRequests.ts");
    expect(client).toContain("fetch(`/api/agent-access/requests/${encodeURIComponent(requestId)}`, { cache: \"no-store\" })");
    expect(client).toMatch(/if \(res\.status === 404\) return null;\s*if \(!res\.ok\) throw new AppRequestError\("The approval did not load/);
  });
});
