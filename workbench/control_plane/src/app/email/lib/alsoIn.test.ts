// WS-17 EM-T8g-3 — "Also in", the UI half (§11.7.7 of
// `project-docs/specs/email_app_master_plan.md`, decision D-EM-22, edge
// case 10).
//
// R7 fence named here:
//   * `email-also-in-row`: the row shows "Also in" and the chip of each
//     mailbox of `also_in`, in the order of the switcher. A member with one
//     mailbox sees none. The mailbox of the row and a mailbox that left the
//     list are skipped. `listEmails` and `searchEmails` carry the field, and
//     a gateway before EM-T8g-3 sends none.
//
// The server half (`email-also-in`, `email-also-in-one-read`) is in
// `tests/unit/test_email_duplicates.py` (R8).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AlsoInLine, alsoInMailboxes } from "../components/EmailList";
import type { EmailAccount } from "./types";

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const box = (id: string, label: string, slot: number): EmailAccount => ({
  id,
  provider: "microsoft",
  emailAddress: `${id}@fracktal.test`,
  label: "",
  displayLabel: label,
  colorSlot: slot,
  unreadCount: 0,
  syncEnabled: true,
});
const work = box("a", "Work", 1);
const sales = box("b", "Sales", 2);
const personal = box("c", "Personal", 3);
const three = [work, sales, personal];

describe("email-also-in-row: the rule", () => {
  it("names each mailbox of also_in, in the order of the switcher", () => {
    const row = { accountId: "a", alsoIn: ["c", "b"] };
    expect(alsoInMailboxes(row, three).map((m) => m.id)).toEqual(["b", "c"]);
  });

  it("names none for a member with one mailbox", () => {
    expect(alsoInMailboxes({ accountId: "a", alsoIn: ["b"] }, [work])).toEqual([]);
    // Also for a row that a removed mailbox left in the list: one mailbox is
    // left, so there is no other mailbox to name (§11.0).
    expect(alsoInMailboxes({ accountId: "a", alsoIn: ["b"] }, [sales])).toEqual([]);
  });

  it("skips the mailbox of the row and a mailbox that left the list", () => {
    const row = { accountId: "a", alsoIn: ["a", "gone", "b"] };
    expect(alsoInMailboxes(row, three).map((m) => m.id)).toEqual(["b"]);
  });

  it("names none when the row has no copy, or the gateway sent no field", () => {
    expect(alsoInMailboxes({ accountId: "a", alsoIn: [] }, three)).toEqual([]);
    expect(alsoInMailboxes({ accountId: "a" }, three)).toEqual([]);
  });
});

describe("email-also-in-row: the row", () => {
  it("draws 'Also in' and the chip of each mailbox", () => {
    const html = renderToStaticMarkup(
      createElement(AlsoInLine, { email: { accountId: "a", alsoIn: ["b", "c"] }, accounts: three }),
    );
    expect(html).toContain(">Also in<");
    expect(html).toContain('aria-label="Mailbox Sales, b@fracktal.test"');
    expect(html).toContain('aria-label="Mailbox Personal, c@fracktal.test"');
    expect(html).not.toContain("Mailbox Work");
    // The label goes with the hue, never a hex value (DESIGN_SYSTEM rule 1).
    expect(html).not.toMatch(/#[0-9a-fA-F]{3,6}\b/);
  });

  it("draws nothing for a member with one mailbox", () => {
    const html = renderToStaticMarkup(
      createElement(AlsoInLine, { email: { accountId: "a", alsoIn: ["b"] }, accounts: [work] }),
    );
    expect(html).toBe("");
  });

  it("draws nothing for a row with no copy", () => {
    const html = renderToStaticMarkup(
      createElement(AlsoInLine, { email: { accountId: "a", alsoIn: [] }, accounts: three }),
    );
    expect(html).toBe("");
  });

  it("is in each row of the list, beside the other mailboxes of the member", () => {
    const list = codeOnly(read("components/EmailList.tsx"));
    const rows = list.slice(list.indexOf("{emails.map((email) => {"));
    expect(rows).toContain("<AlsoInLine email={email} accounts={accounts} />");
    // Each label is a MailboxChip, the one chip of a mailbox.
    expect(list).toContain("<MailboxChip key={box.id} account={box} />");
  });
});

describe("email-also-in-row: the API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const reply = (rows: Array<Record<string, unknown>>) =>
    vi.fn(async () =>
      new Response(JSON.stringify({ emails: rows, total: rows.length, page: 1, page_size: 50 }), {
        status: 200,
      }),
    );

  it("maps also_in from the list and from search", async () => {
    const rows = [
      { id: "m1", account_id: "a", also_in: ["b", "c"] },
      { id: "m2", account_id: "a" },
      { id: "m3", account_id: "a", also_in: ["b", 7, "", null] },
      { id: "m4", account_id: "a", also_in: "b" },
    ];
    const api = await import("./api");
    vi.stubGlobal("fetch", reply(rows));
    const listed = await api.listEmails({});
    vi.stubGlobal("fetch", reply(rows));
    const found = await api.searchEmails({ q: "quote" });
    for (const out of [listed, found]) {
      expect(out.emails.map((e) => e.alsoIn)).toEqual([["b", "c"], [], ["b"], []]);
    }
  });
});
