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
//     `MailboxAvatar`, and no file under `app/email` reads `account.color`,
//     `avatar_color` or `#6366f1`. The test walks the whole directory.
//   * `email-mailbox-slot-convert`: the stored slot is 1-based and the ramp is
//     0-based, and the dialog converts in one place.
//   * `email-mailbox-colour-save`: a save of the colour alone sends no label,
//     so it never rewrites a chosen name. The name field seeds from the trimmed
//     chosen label.
//   * `email-slot-picker-shared`: Space settings and the mailbox dialog draw
//     the one `SlotPicker`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
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
import { seedLabel, slotToStored, storedToSlot } from "../components/MailboxEditDialog";

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

describe("the reading pane names the mailbox of the mail (§11.4)", () => {
  const detail = codeOnly(read("components/EmailDetail.tsx"));

  it("draws the chip and the address for two or more mailboxes", () => {
    expect(detail).toContain("const mailboxAccount = accounts.find((a) => a.id === mailboxId);");
    expect(detail).toContain("{mailboxAccount && accounts.length > 1 && (");
    expect(detail).toContain("<MailboxChip account={mailboxAccount} />");
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
    const walk = (dir: string): string[] =>
      readdirSync(dir).flatMap((name) => {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) return walk(path);
        return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
      });
    const files = walk(ROOT);
    expect(files.length).toBeGreaterThan(20);
    for (const path of files) {
      const rel = relative(ROOT, path);
      const src = codeOnly(readFileSync(path, "utf-8"));
      expect(src, rel).not.toMatch(/account\.color\b|selectedAccount\.color\b|avatar_color/);
      expect(src, rel).not.toContain("#6366f1");
    }
  });
});

describe("a save of the colour alone keeps the name (EM-T8b review)", () => {
  it("seeds the field from the trimmed chosen label, and blank for none", () => {
    expect(seedLabel({ label: "Work ", displayLabel: "Work" })).toBe("Work");
    expect(seedLabel({ label: "Outlook", displayLabel: "Fracktal" })).toBe("");
    expect(seedLabel({ label: "", displayLabel: "Fracktal" })).toBe("");
  });

  it("sends the label only when the member changed the field", () => {
    const dialog = codeOnly(read("components/MailboxEditDialog.tsx"));
    expect(dialog).toContain("...(labelTouched ? { label: label.trim() } : {}),");
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("...(edit.label !== undefined ? { label: edit.label } : {}),");
  });

  it("previews a blank name with the default label of the gateway", () => {
    const dialog = codeOnly(read("components/MailboxEditDialog.tsx"));
    expect(dialog).toContain("const fallback = account.defaultLabel || account.emailAddress;");
  });
});

describe("one colour picker for the product", () => {
  it("is drawn by Space settings and by the mailbox dialog", () => {
    const space = readFileSync(
      join(ROOT, "..", "projects", "components", "SpaceSettings.tsx"), "utf-8");
    expect(space).toContain('<SlotPicker value={slot} onChange={setSlot} label="Icon colour" />');
    expect(read("components/MailboxEditDialog.tsx")).toContain(
      '<SlotPicker value={slot} onChange={setSlot} label="Mailbox colour" />');
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
