/**
 * The email and CRM reads draw through the one `Readout` (follow-up of #716
 * and #735, spec `projects_ai_chat.md` §24.8). Their cards drew the tool's
 * text as it is: `id=<uuid>`, `(id=<uuid>)`, `• status_id: <uuid>` and raw
 * `[open]` keys.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - `InfoResultCard` draws `{text}` again -> "an email read shows no id";
 * - `ActionResultCard` draws `{detail}` again -> "an email write receipt
 *   shows no id";
 * - `crmEvidence` returns null, or MessageBubble drops it -> "a CRM read
 *   draws in its step";
 * - `withoutIds` loses the `id=` forms -> "shows no id";
 * - the `• key: value` rule goes -> "labels each fact";
 * - a `[kind]` draws as the key -> "draws a stage as a chip";
 * - the thread fallback drops a body line "Done: …" (review round 1) ->
 *   "a thread's fallback keeps each body line";
 * - an unfenced address draws as text -> "labels each fact of a record"
 *   (the person chip's initials).
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import { crmEvidence } from "@/components/crm/CrmEvidence";
import EmailToolCards, { emailEvidence, threadText } from "@/components/email/EmailToolCards";
import type { ToolEvent } from "@/components/MarkdownMessage";
import { parseReadout } from "@/lib/readout";
import { statusAccent } from "@/lib/statusAccent";

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const ID2 = "1e5953b5-0000-4000-8000-000000000001";

function ev(name: string, result: string): ToolEvent {
  return { id: `e-${name}`, name, args: {}, result, status: "done", startedAt: 1, endedAt: 2 };
}

const html = (node: React.ReactNode) => renderToStaticMarkup(createElement("div", null, node));
const visible = (markup: string) => markup.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
const NO_ID = (markup: string) => {
  expect(markup).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-/);
  expect(visible(markup)).not.toMatch(/\bid=|_id\b|\(\s*\)/);
};

/** `agent-email-assistant/agents.py` `list_accounts`. */
const ACCOUNTS = [
  "Connected accounts (5 unread total):",
  `• Work · vijay@x.io — id=${ID}, 3 unread`,
  `• Personal · v@gmail.com (separate) — id=${ID2}, 2 unread`,
  "The total leaves out each separate mailbox, as All inboxes does.",
].join("\n");

/** `list_rule_history`. */
const HISTORY = ["Recent rule activity:", `• id=${ID} [pending] Newsletters: Weekly news → archive`].join("\n");

/** `agent-crm/agents.py` `get_record`. */
const RECORD = [
  `Lead: Ravi Kumar (id=${ID})`,
  "• lead_name: Ravi Kumar",
  "• email: ravi@acme.io",
  "• owner_email: priya@x.io",
  `• status_id: ${ID2}`,
  "• status: Qualified",
  "• created_at: 2026-10-01T10:00:00+00:00",
].join("\n");

/** `search_crm`. */
const SEARCH = [
  "Matches for 'acme' (2):",
  "",
  "Leads (1):",
  `• Ravi Kumar @ Acme · ravi@acme.io · owner priya@x.io (id=${ID})`,
  "",
  "Deals (1):",
  `• Acme rollout · INR 50,000 @ Acme · closes 2026-11-01 · owner priya@x.io (id=${ID2})`,
].join("\n");

/** `get_pipeline`. */
const PIPELINE = [
  "Deal pipeline:",
  "",
  "Negotiation [open] — 1 deal · INR 50,000",
  `• Acme rollout · INR 50,000 @ Acme · closes 2026-11-01 (id=${ID2})`,
  "",
  "Total: 1 deal · INR 50,000 across 1 stages.",
].join("\n");

describe("the email reads", () => {
  it("an email read shows no id", () => {
    const out = html(emailEvidence(ev("list_accounts", ACCOUNTS)));
    NO_ID(out);
    expect(visible(out)).toContain("vijay@x.io");
    expect(visible(out)).toContain("3 unread");
  });

  it("draws a leading [kind] as a chip, not as the key", () => {
    const out = html(emailEvidence(ev("list_rule_history", HISTORY)));
    NO_ID(out);
    expect(visible(out)).not.toContain("[pending]");
    expect(visible(out)).toContain("Pending");
  });

  it("an email write receipt shows no id", () => {
    const out = renderToStaticMarkup(
      createElement(EmailToolCards, { toolEvents: [ev("move_to_folder", `Moved 1 email to Archive (id=${ID}).`)] }),
    );
    expect(out).toContain("Moved to folder");
    NO_ID(out);
    expect(visible(out)).toContain("Moved 1 email to Archive.");
  });

  it("a thread's fallback keeps each body line, and drops only the ids", () => {
    // Review round 1: `Readout` read a body line "Done: …" as a machine line.
    const raw = [`Thread: PO — 2 messages, oldest first: (id=${ID})`, "Done: the PO is signed", "- item one", "Agenda:"].join("\n");
    const out = threadText(raw);
    expect(out).toContain("Done: the PO is signed");
    expect(out).toContain("- item one");
    expect(out).toContain("Agenda:");
    expect(out).not.toContain(ID);
  });

  it("no email card draws a result as it is", () => {
    // The four fallbacks drew `{event.result}`, `{text}`, `{detail}` and `{result}`.
    const src = readFileSync(new URL("./email/EmailToolCards.tsx", import.meta.url), "utf8");
    // Each sat alone on its line, as a child of a text box.
    expect(src).not.toMatch(/^\s*\{(?:event\.result|text \|\| "\(no result\)"|detail|result)\}\s*$/m);
  });
});

describe("the CRM reads", () => {
  it("a CRM read draws in its step, and only a read", () => {
    expect(crmEvidence(ev("get_record", RECORD))).not.toBeNull();
    expect(crmEvidence(ev("crm-assistant__search_crm", SEARCH))).not.toBeNull();
    expect(crmEvidence(ev("create_lead", "Created lead Ravi"))).toBeNull();
    expect(crmEvidence({ ...ev("get_record", RECORD), status: "running" })).toBeNull();
    // MessageBubble hands every step to the CRM evidence too.
    const bubble = readFileSync(new URL("./MessageBubble.tsx", import.meta.url), "utf8");
    expect(bubble).toMatch(/\?\? crmEvidence\(e\)/);
  });

  it("labels each fact of a record, and shows no id", () => {
    const blocks = parseReadout(RECORD);
    expect(blocks.filter((b) => b.kind === "field").map((b) => b.kind === "field" && b.key)).toEqual([
      "lead_name", "email", "owner_email", "status", "created_at",
    ]);
    const out = html(crmEvidence(ev("get_record", RECORD)));
    NO_ID(out);
    const text = visible(out);
    for (const label of ["Lead", "Email", "Owner", "Status", "Created"]) expect(text).toContain(label);
    expect(text).not.toContain("lead_name");
    expect(text).not.toContain("owner_email");
    expect(text).toContain("1 Oct 2026");
    // A status is its chip, and an address is a person chip with initials.
    expect(out).toContain(statusAccent({ name: "Qualified" }).dot);
    expect(out).toContain(">PR<");
    expect(out).toContain(">RA<");
  });

  it("a search shows its rows with no id", () => {
    const out = html(crmEvidence(ev("search_crm", SEARCH)));
    NO_ID(out);
    expect(visible(out)).toContain("Ravi Kumar @ Acme");
    expect(visible(out)).toContain("owner priya@x.io");
  });

  it("draws a stage as a chip", () => {
    const out = html(crmEvidence(ev("get_pipeline", PIPELINE)));
    NO_ID(out);
    expect(visible(out)).not.toContain("[open]");
    expect(visible(out)).toContain("Open");
    expect(visible(out)).toContain("Negotiation");
  });
});
