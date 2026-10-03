// WS-17 EM-T8b — the mailbox identity on screen (§11.4 and §11.7.2 of
// `project-docs/specs/email_app_master_plan.md`, decision D-EM-21, MB-8).
//
// R7 fences named here:
//   * `email-mailbox-chip-ramp`: the chip and the avatar take their hue from
//     the categorical ramp through `colorSlot`, and a mailbox with no slot
//     hashes its id. No hex value and no `style` colour.
//   * `email-mailbox-chip-label`: the hue never stands alone. The chip draws
//     the label and names the address.
//   * `email-mailbox-no-hex`: the account sidebar and the mobile top bar draw
//     `MailboxAvatar`, and no email file reads `account.color` or `avatar_color`.
//   * `email-mailbox-slot-convert`: the stored slot is 1-based and the ramp is
//     0-based, and the dialog converts in one place.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { accentForSlot, categoricalAccent } from "@/lib/categorical";
import {
  MailboxAvatar,
  MailboxChip,
  mailboxAccent,
  mailboxInitial,
  mailboxLabel,
} from "../components/MailboxChip";
import { slotToStored, storedToSlot } from "../components/MailboxEditDialog";

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const box = {
  id: "box-a",
  emailAddress: "vj@fracktal.in",
  displayLabel: "Fracktal",
  colorSlot: 3 as number | null,
};

describe("the hue of a mailbox comes from the ramp", () => {
  it("maps the stored slot 1 to 12 onto the 0-based ramp", () => {
    expect(mailboxAccent({ id: "x", colorSlot: 1 })).toEqual(accentForSlot(0));
    expect(mailboxAccent({ id: "x", colorSlot: 12 })).toEqual(accentForSlot(11));
  });

  it("hashes the id when there is no slot, or a slot outside the ramp", () => {
    for (const colorSlot of [null, undefined, 0, 13]) {
      expect(mailboxAccent({ id: "box-z", colorSlot })).toEqual(categoricalAccent("box-z"));
    }
  });
});

describe("the label goes with the hue", () => {
  it("draws the display label, else the address", () => {
    expect(mailboxLabel(box)).toBe("Fracktal");
    expect(mailboxLabel({ ...box, displayLabel: "  " })).toBe("vj@fracktal.in");
    expect(mailboxLabel({ ...box, displayLabel: undefined })).toBe("vj@fracktal.in");
  });

  it("takes the first letter or digit of the label", () => {
    expect(mailboxInitial(box)).toBe("F");
    expect(mailboxInitial({ ...box, displayLabel: "  @sales" })).toBe("S");
    expect(mailboxInitial({ ...box, displayLabel: "élan" })).toBe("É");
  });

  it("renders the chip with the dot, the label and the address", () => {
    const html = renderToStaticMarkup(createElement(MailboxChip, { account: box }));
    const accent = accentForSlot(2);
    expect(html).toContain(accent.dot);
    expect(html).toContain(">Fracktal<");
    expect(html).toContain('title="vj@fracktal.in"');
    expect(html).toContain('aria-label="Mailbox Fracktal, vj@fracktal.in"');
    expect(html).not.toMatch(/style=|#[0-9a-f]{6}/i);
  });

  it("renders the avatar with the initial in the tint of the slot", () => {
    const html = renderToStaticMarkup(createElement(MailboxAvatar, { account: box }));
    expect(html).toContain(accentForSlot(2).chip);
    expect(html).toContain(">F<");
    expect(html).toContain('title="Fracktal · vj@fracktal.in"');
    expect(html).not.toMatch(/style=|text-white|#[0-9a-f]{6}/i);
  });
});

describe("no email surface draws the old hex colour", () => {
  it("draws the avatar component in the sidebar and the mobile top bar", () => {
    expect(codeOnly(read("components/AccountSidebar.tsx"))).toContain(
      "<MailboxAvatar account={account} />",
    );
    expect(codeOnly(read("page.tsx"))).toContain(
      "<MailboxAvatar account={selectedAccount} />",
    );
  });

  it("reads no hex colour of an account anywhere in Email", () => {
    for (const file of [
      "components/AccountSidebar.tsx",
      "page.tsx",
      "lib/api.ts",
      "lib/types.ts",
      "lib/mockData.ts",
    ]) {
      const src = codeOnly(read(file));
      expect(src, file).not.toMatch(/account\.color\b|selectedAccount\.color\b|avatar_color/);
      expect(src, file).not.toContain("#6366f1");
    }
  });
});

describe("the dialog converts the slot in one place", () => {
  it("round-trips 1 to 12 and refuses the rest", () => {
    for (let stored = 1; stored <= 12; stored++) {
      expect(slotToStored(storedToSlot(stored))).toBe(stored);
    }
    for (const bad of [null, undefined, 0, 13]) expect(storedToSlot(bad)).toBe(-1);
  });
});
