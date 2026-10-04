// WS-17 EM-T6e: the dialog "Remove older mail from Metorite", drawn.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6e",
// done-whens A4, A8, A10 and A11, and the UI contract D5.
//
// R7 fences named here:
//   * `email-storage-dialog-name` (A4): two or more mailboxes draw the chip,
//     the address and the label. One mailbox reads "This mailbox".
//   * `email-storage-dialog-copy` (A10): the Metorite-only words, and a scan
//     of every word of the notice, the step and the dialog for a sentence that
//     says Metorite deletes mail in Outlook.
//   * `email-storage-dialog-busy` (A11): a 409 shows the detail of the gateway,
//     and the choice stays checked.
//   * `email-storage-dialog-ui` (D5): `Modal`, `Button`s, `Input type="date"`
//     with `max`, and no hand-rolled overlay.
//
// vitest here runs in the node environment and reads `*.test.ts` only. A
// `Modal` portal draws nothing there, so the test draws the pure
// `RemoveOlderMailView` with `createElement` and `renderToStaticMarkup`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";

import {
  RemoveOlderMailView,
  previewText,
  type RemoveOlderMailViewProps,
  type RemovalAccount,
} from "../components/RemoveOlderMailDialog";
import { StorageNotice } from "../components/StorageNotice";
import { StorageStep } from "../components/StorageStep";
import {
  BYTES_PER_MB,
  STORAGE_COPY,
  initialRemovalState,
  providerUnchanged,
  removalFailure,
  removalReducer,
  type RemovalEvent,
  type RemovalState,
} from "./storage";

const MB = BYTES_PER_MB;
const EMAIL_APP = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(EMAIL_APP, rel), { encoding: "utf-8" }).replace(/\r\n/g, "\n");
const codeOnly = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
const noop = () => {};
const DISABLED = /(?:^|\s)disabled=""/;

const ACCOUNT: RemovalAccount = {
  id: "a",
  emailAddress: "vj@fracktal.in",
  displayLabel: "Fracktal",
  colorSlot: 1,
  provider: "microsoft",
};

const KEY = new Date(2026, 6, 6).toISOString();
const ECHO = "2026-07-05T18:30:00+00:00";
const run = (s: RemovalState, ...events: RemovalEvent[]) => events.reduce(removalReducer, s);
const picked = () => run(initialRemovalState(), { type: "pick", choice: { kind: "months", months: 3, before: KEY } });
const ready = (messages = 12_400) =>
  run(picked(), { type: "previewAnswered", key: KEY, answer: { before: ECHO, messages, bytes: 380 * MB } });

function view(over: Partial<RemoveOlderMailViewProps>): string {
  const props: RemoveOlderMailViewProps = {
    account: ACCOUNT,
    named: false,
    state: initialRemovalState(),
    dateMax: "2026-10-04",
    locale: "en-US",
    onPickMonths: noop,
    onPickDate: noop,
    onDateChange: noop,
    onConfirm: noop,
    onClose: noop,
    ...over,
  };
  return renderToStaticMarkup(createElement(RemoveOlderMailView, props));
}

function buttons(markup: string): Array<{ text: string; attrs: string }> {
  return [...markup.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map((m) => ({
    attrs: m[1],
    text: m[2].replace(/<[^>]*>/g, "").trim(),
  }));
}

const confirmButton = (markup: string) => buttons(markup).find((b) => b.text === STORAGE_COPY.confirm);

/** Text of the markup with each tag removed, so a sentence reads whole. */
const text = (markup: string) => markup.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");

describe("email-storage-dialog-copy (A10, D-EM-14)", () => {
  it("holds the Metorite-only sentences before any choice", () => {
    const out = text(view({}));
    expect(out).toContain("This removes mail from Metorite only.");
    expect(out).toContain("Your Outlook mailbox does not change.");
    expect(out).toContain(STORAGE_COPY.kept);
    expect(out).toContain(STORAGE_COPY.loadOlderNote);
  });

  it("names the provider that does not change", () => {
    expect(providerUnchanged("microsoft")).toBe("Your Outlook mailbox does not change.");
    expect(providerUnchanged(undefined)).toBe("Your Outlook mailbox does not change.");
    expect(providerUnchanged("gmail")).toBe("Your Gmail mailbox does not change.");
  });

  it("no word of the notice, the step or the dialog says Metorite deletes mail in Outlook", () => {
    const FULL = {
      ...ACCOUNT,
      storedBytes: 512 * MB,
      storageLimitBytes: 500 * MB,
      importPhase: "limit",
      importReachedAt: null,
    };
    const states: RemovalState[] = [
      initialRemovalState(),
      ready(),
      run(ready(), { type: "confirm", before: ECHO }),
      run(ready(), { type: "confirm", before: ECHO }, { type: "removeFailed", failure: { kind: "follow" }, now: 0 }),
      run(ready(), { type: "confirm", before: ECHO }, {
        type: "removed",
        result: { before: ECHO, removed: 12, storedBytes: 9 * MB, storageLimitBytes: 500 * MB },
      }),
    ];
    const markup = [
      ...states.flatMap((state) => [view({ state }), view({ state, named: true })]),
      renderToStaticMarkup(createElement(StorageNotice, { account: FULL, named: true, onRemove: noop })),
      renderToStaticMarkup(createElement(StorageNotice, { account: { ...FULL, storedBytes: MB }, named: false, onRemove: noop })),
      renderToStaticMarkup(createElement(StorageStep, { account: FULL, named: true, onRemove: noop, onKeep: noop })),
    ];
    const words = [...markup.map(text), ...Object.values(STORAGE_COPY)].join(" . ");
    expect(words).not.toMatch(/delet\w*[^.]*outlook/i);
    expect(words).not.toMatch(/outlook[^.]*delet\w*/i);
    // The scan can fail: a sentence that says so is caught.
    expect("Metorite deletes this mail in Outlook.").toMatch(/delet\w*[^.]*outlook/i);
  });
});

describe("email-storage-dialog-name (A4, D3)", () => {
  it("two or more mailboxes: the chip and the address", () => {
    const out = view({ named: true });
    expect(out).toContain('aria-label="Mailbox Fracktal, vj@fracktal.in"');
    expect(out).toContain(">vj@fracktal.in<");
  });

  it("two or more mailboxes: the meter line names the label", () => {
    const done = run(ready(), { type: "confirm", before: ECHO }, {
      type: "removed",
      result: { before: ECHO, removed: 12_400, storedBytes: 132 * MB, storageLimitBytes: 500 * MB },
    });
    expect(text(view({ named: true, state: done }))).toContain("Fracktal now uses 132 MB of its 500 MB in Metorite.");
    expect(text(view({ named: false, state: done }))).toContain("This mailbox now uses 132 MB of its 500 MB in Metorite.");
    expect(text(view({ state: done }))).toContain("Metorite removed 12,400 messages.");
  });

  it("one mailbox: no chip", () => {
    expect(view({ named: false })).not.toContain("Mailbox Fracktal");
  });

  it("the notice names the label with two mailboxes, and 'This mailbox' with one (A4)", () => {
    const full = { ...ACCOUNT, storedBytes: 512 * MB, storageLimitBytes: 500 * MB, importPhase: "limit" };
    const two = renderToStaticMarkup(createElement(StorageNotice, { account: full, named: true, onRemove: noop, locale: "en-US" }));
    expect(two).toContain('aria-label="Mailbox Fracktal, vj@fracktal.in"');
    expect(text(two)).toContain("Fracktal uses 512 MB of its 500 MB in Metorite.");
    const one = renderToStaticMarkup(createElement(StorageNotice, { account: full, named: false, onRemove: noop, locale: "en-US" }));
    expect(one).not.toContain("Mailbox Fracktal");
    expect(text(one)).toContain("This mailbox uses 512 MB of its 500 MB in Metorite.");
  });
});

describe("the confirm on screen (A8)", () => {
  it("is disabled with no choice, while the preview runs, and at 0 messages", () => {
    for (const state of [initialRemovalState(), picked(), ready(0)]) {
      expect(confirmButton(view({ state }))?.attrs).toMatch(DISABLED);
    }
    expect(text(view({ state: picked() }))).toContain(STORAGE_COPY.counting);
    expect(text(view({ state: ready(0) }))).toContain(STORAGE_COPY.previewNone);
  });

  it("is enabled once the preview of the current choice answers, with the count", () => {
    const out = view({ state: ready() });
    expect(confirmButton(out)?.attrs).not.toMatch(DISABLED);
    expect(text(out)).toContain("12,400 messages, about 380 MB");
  });

  it("is disabled while the removal runs, and Cancel too", () => {
    const out = view({ state: run(ready(), { type: "confirm", before: ECHO }) });
    expect(confirmButton(out)?.attrs).toMatch(DISABLED);
    expect(buttons(out).find((b) => b.text === STORAGE_COPY.cancel)?.attrs).toMatch(DISABLED);
    expect(text(out)).toContain(STORAGE_COPY.removing);
  });

  it("the date choice needs a day in the past before it counts", () => {
    const dated = run(initialRemovalState(), { type: "pick", choice: { kind: "date", date: "", before: null } });
    expect(previewText(dated)).toBe(STORAGE_COPY.chooseDay);
    expect(previewText(initialRemovalState())).toBe(STORAGE_COPY.chooseFirst);
    const future = run(dated, { type: "pick", choice: { kind: "date", date: "2026-12-01", before: null } });
    expect(previewText(future)).toBe(STORAGE_COPY.dateInvalid);
    expect(confirmButton(view({ state: future }))?.attrs).toMatch(DISABLED);
  });
});

describe("email-storage-dialog-busy (A11)", () => {
  const BUSY = "A sync is running for this mailbox. Try again when it ends.";
  const err = Object.assign(new Error(BUSY), { status: 409 });

  it("shows the detail of the gateway as it is, and keeps the choice", () => {
    const state = run(ready(), { type: "confirm", before: ECHO }, {
      type: "removeFailed",
      failure: removalFailure(err),
      now: 0,
    });
    const out = view({ state });
    expect(out).toMatch(new RegExp(`role="alert"[^>]*>${BUSY.replace(/\./g, "\\.")}<`));
    const checked = buttons(out).filter((b) => b.attrs.includes('aria-checked="true"'));
    expect(checked.map((b) => b.text)).toEqual(["3 months"]);
    // The member can confirm again without a new pick.
    expect(confirmButton(out)?.attrs).not.toMatch(DISABLED);
  });
});

describe("email-storage-dialog-ui (D5)", () => {
  const src = codeOnly(read("components/RemoveOlderMailDialog.tsx"));

  it("is built on Modal, Button and Input, with no hand-rolled overlay", () => {
    expect(src).toContain('import Modal from "@/components/ui/Modal";');
    expect(src).toContain('import Button from "@/components/ui/Button";');
    expect(src).toContain('import Input from "@/components/ui/Input";');
    for (const f of ["components/RemoveOlderMailDialog.tsx", "components/StorageNotice.tsx", "components/StorageStep.tsx"]) {
      const code = codeOnly(read(f));
      expect(code, f).not.toMatch(/fixed inset-0|@base-ui\/react|ConfirmDialog|<button\b|<input\b/);
      expect(code, f).not.toMatch(/amber|yellow|emerald|#[0-9a-f]{3,6}\b/i);
    }
  });

  it("the date is Input type date with max today, and the months are a radio group of Buttons", () => {
    const out = view({ state: run(initialRemovalState(), { type: "pick", choice: { kind: "date", date: "", before: null } }) });
    expect(out).toMatch(/<input[^>]*type="date"[^>]*max="2026-10-04"/);
    expect(out).toContain('role="radiogroup"');
    const radios = buttons(out).filter((b) => b.attrs.includes('role="radio"'));
    expect(radios.map((b) => b.text)).toEqual(["1 month", "2 months", "3 months", "6 months", "A date"]);
  });

  it("names only icons that exist", () => {
    for (const f of ["components/RemoveOlderMailDialog.tsx", "components/StorageNotice.tsx", "components/StorageStep.tsx"]) {
      // Every quoted name inside a `name=` or `icon=` attribute, a ternary too.
      const attrs = [...read(f).matchAll(/\b(?:name|icon)=(\{[^}]*\}|"[^"]*")/g)].map((m) => m[1]);
      const names = attrs.flatMap((a) => [...a.matchAll(/"([A-Z][A-Za-z0-9]+)"/g)].map((m) => m[1]));
      expect(names.length, f).toBeGreaterThan(0);
      for (const n of names) expect(isKnownIcon(n), `${f}: ${n}`).toBe(true);
    }
  });
});
