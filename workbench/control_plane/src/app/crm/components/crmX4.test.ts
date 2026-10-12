// Record page v2 (WS-53 CRM-X4, spec section 19.8 and the notes of 19.10).
//
// The runner has no DOM, so each component is rendered to a string. This file
// holds done-when items 1, 6 and 7. `recordNav.test.ts` holds item 5,
// `timeline.test.ts` item 3, `urlState.test.ts` item 2 and `format.test.ts`
// item 4.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { contactValue, formatField, isEmail, summaryFacts, telHref, type FieldDef } from "../lib/fields";
import { RECORD_PAGE_ATTR } from "../lib/recordNav";
import type { RecordTab } from "../lib/urlState";
import type { EntitySlug, Status, TimelineEntry } from "../lib/types";
import FieldValue from "./FieldValue";
import FieldsPanel from "./FieldsPanel";
import { RecordPage } from "./RecordSheet";

const noop = () => undefined;
const CRM = fileURLToPath(new URL("..", import.meta.url));

// ── Item 1: no raw control ────────────────────────────────────────────────

/** Every component file of the CRM, tests left out. */
function sources(dir: string = CRM): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return name.endsWith(".tsx") ? [path] : [];
  });
}

/** Comments removed. A comment that names `<input` is not a control. */
const strip = (text: string) =>
  text.replace(/(?<=^|[\s{(])\/\*[\s\S]*?\*\//g, "").replace(/(?<![:"'/])\/\/[^\n]*/g, "");

/** Each raw `<input`, `<textarea` or `<select` tag. A hidden file input is allowed. */
function rawControls(text: string): string[] {
  const found: string[] = [];
  for (const m of strip(text).matchAll(/<(input|textarea|select)\b[\s\S]*?\/?>/g)) {
    const tag = m[0];
    const hiddenFile = /type="file"/.test(tag) && /className="hidden"/.test(tag);
    if (!hiddenFile) found.push(m[1]);
  }
  return found;
}

describe("no raw control under app/crm (CRM-X4 item 1, design rule 3)", () => {
  it("finds the CRM's component files", () => {
    expect(sources().length).toBeGreaterThan(20);
  });

  it("holds no raw <input>, <textarea> or <select> tag", () => {
    const offenders = sources()
      .map((path) => [path.slice(CRM.length), rawControls(readFileSync(path, "utf8"))] as const)
      .filter(([, tags]) => tags.length > 0);
    expect(
      offenders,
      "Use Input, Textarea or SelectButton from src/components/ui/. A raw control takes the browser's paint.",
    ).toEqual([]);
  });

  it("catches a raw control, and lets a hidden file input through", () => {
    expect(rawControls('<input value={x} onChange={f} />')).toEqual(["input"]);
    expect(rawControls("<textarea rows={2} />")).toEqual(["textarea"]);
    expect(rawControls("<select value={x}>")).toEqual(["select"]);
    expect(rawControls('<input type="file" className="hidden" onChange={f} />')).toEqual([]);
    expect(rawControls("<Input value={x} />")).toEqual([]);
    expect(rawControls("// an <input> in a comment")).toEqual([]);
  });
});

// ── Item 6: tel: and mailto: ──────────────────────────────────────────────

const phoneDef: FieldDef = {
  key: "alt_phone",
  label: "Alt phone",
  type: "phone",
  options: null,
  position: 0,
  visible: true,
  source: "zoho",
  api_name: "Alt_Phone",
};

describe("phone and email links (CRM-X4 item 6)", () => {
  it("gives a phone of 6 digits or more a tel: link of digits and a leading +", () => {
    expect(formatField(phoneDef, "+91 98450-12345")).toEqual({
      kind: "tel",
      href: "tel:+919845012345",
      text: "+91 98450-12345",
    });
    expect(telHref("(080) 4123 4567")).toBe("tel:08041234567");
    expect(telHref("123456")).toBe("tel:123456");
  });

  it("keeps a shorter value as text", () => {
    expect(telHref("12345")).toBeNull();
    expect(formatField(phoneDef, "ext 12")).toEqual({ kind: "text", text: "ext 12" });
  });

  it("never lets a letter or a query into the href", () => {
    // A `;` means two values, so the whole value stays text.
    expect(telHref("+91 98450 12345;ext=9?x=javascript:1")).toBeNull();
    for (const value of ["+91 98450 12345", "(080) 4123-4567", "+91.80.4123.4567"]) {
      expect(telHref(value)).toMatch(/^tel:\+?\d+$/);
    }
  });

  // Review P2: one link never joins the digits of two things.
  it("dials the number of a value with an extension, not the extension", () => {
    expect(telHref("+91 98450 12345 x56")).toBe("tel:+919845012345");
    expect(telHref("+91 98450 12345 ext. 56")).toBe("tel:+919845012345");
    expect(telHref("080 4123 4567 #2")).toBe("tel:08041234567");
    expect(telHref("080 4123 4567 X2")).toBe("tel:08041234567");
  });

  it("keeps a value that holds two numbers as text", () => {
    for (const value of [
      "98450 12345 / 98450 54321",
      "98450 12345, 98450 54321",
      "98450 12345; 98450 54321",
      "98450 12345 98450 54321",
      "98450 12345 or 98450 54321",
      "98450 12345 +91 98450 54321",
    ]) {
      expect(telHref(value), value).toBeNull();
      expect(formatField(phoneDef, value), value).toEqual({ kind: "text", text: value });
    }
  });

  it("never passes a list of addresses as one mailto:", () => {
    for (const value of ["sales@bosch.in,info", "sales@bosch.in;info", "a@b.in,c@d.in", "a@b.in, c@d.in"]) {
      expect(isEmail(value), value).toBe(false);
      expect(contactValue("email", value), value).toEqual({ kind: "text", text: value });
    }
  });

  it("draws the tel kind as a link", () => {
    const html = renderToStaticMarkup(createElement(FieldValue, { value: formatField(phoneDef, "+91 98450 12345") }));
    expect(html).toContain('href="tel:+919845012345"');
  });

  it("exports the isEmail guard", () => {
    expect(isEmail("anitha@bosch.in")).toBe(true);
    expect(isEmail("a@b.com?bcc=x")).toBe(false);
  });

  const panel = (entity: EntitySlug, record: Record<string, unknown>) =>
    renderToStaticMarkup(
      createElement(FieldsPanel, {
        entity,
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

  it("links the fixed email, phone and mobile fields of a contact", () => {
    const html = panel("contacts", {
      id: "c1",
      first_name: "Anitha",
      email: "anitha@bosch.in",
      phone: "+91 80 4123 4567",
      mobile: "98450 12345",
    });
    expect(html).toContain('href="mailto:anitha@bosch.in"');
    expect(html).toContain('href="tel:+918041234567"');
    expect(html).toContain('href="tel:9845012345"');
  });

  it("gives a fixed email mailto: only when isEmail passes", () => {
    const html = panel("leads", { id: "l1", lead_name: "Bosch", email: "a@b.com?bcc=x", phone: "123" });
    expect(html).not.toContain("mailto:");
    expect(html).not.toContain("tel:");
    expect(html).toContain("a@b.com?bcc=x");
  });

  it("keeps the links on a mirrored record, where the fields are locked", () => {
    const html = panel("organizations", {
      id: "o1",
      name: "Bosch",
      phone: "+91 80 4123 4567",
      email: "sales@bosch.in",
      ext_source: "zoho",
    });
    expect(html).toContain('href="tel:+918041234567"');
    expect(html).toContain('href="mailto:sales@bosch.in"');
    expect(html).toContain("Synced from Zoho. Change it in Zoho.");
  });
});

// ── Item 7: the header and the tabs, also in Mirror mode ──────────────────

const stages: Status[] = [
  { id: "s1", name: "Qualified", color: "blue", position: 0, type: "open", is_default: true },
  { id: "s2", name: "Proposal", color: "amber", position: 1, type: "open", is_default: false },
];

const mirroredDeal = {
  id: "d2",
  name: "Bosch order",
  status_id: "s2",
  amount: 250000,
  currency: "INR",
  owner_email: "priya@fracktal.in",
  expected_close_date: "2026-11-30",
  ext_source: "zoho",
};

function page(over: {
  entity?: EntitySlug;
  record?: Record<string, unknown>;
  tab?: RecordTab;
  order?: string[] | null;
  timeline?: TimelineEntry[];
  companyName?: string | null;
}) {
  return renderToStaticMarkup(
    createElement(RecordPage, {
      entity: over.entity ?? "deals",
      record: over.record ?? mirroredDeal,
      statuses: over.entity && over.entity !== "deals" ? [] : stages,
      timeline: over.timeline ?? [],
      dealContacts: [],
      saving: false,
      fieldDefs: null,
      tab: over.tab ?? "overview",
      onTab: noop,
      order: over.order ?? null,
      onStep: noop,
      companyName: over.companyName ?? null,
      now: new Date("2026-10-12T06:00:00Z"),
      onPatch: noop,
      onMoveStatus: noop,
      onLog: noop,
      onToggleTask: noop,
      onConvert: noop,
      onSetPrimary: noop,
      onDetach: noop,
      onOpenContact: noop,
    }),
  );
}

const tabs = (html: string) =>
  [...html.matchAll(/<button[^>]*role="tab"[^>]*>([\s\S]*?)<\/button>/g)].map((m) =>
    m[1].replace(/<[^>]*>/g, "").trim(),
  );

describe("the record page in Mirror mode (CRM-X4 item 7)", () => {
  it("shows the summary header of a deal: the stage, the amount, the owner and the close date", () => {
    const html = page({});
    expect(html).toContain("Proposal");
    expect(html).toContain("2,50,000");
    expect(html).toContain("priya@fracktal.in");
    expect(html).toContain("30 Nov");
  });

  it("shows the four tabs", () => {
    expect(tabs(page({}))).toEqual(["Overview", "Timeline", "Related", "Fields"]);
  });

  it("keeps the fields locked, and the stage pill shut", () => {
    const html = page({});
    expect(html).toContain("Synced from Zoho. Change it in Zoho.");
    // No field is a button that starts an edit, and the pill opens no menu.
    expect(html).not.toMatch(/<button[^>]*>Bosch order<\/button>/);
    expect(html).not.toContain('role="combobox"');
  });

  it("opens the stage pill as a control on a native deal", () => {
    expect(page({ record: { ...mirroredDeal, ext_source: null } })).toContain('role="combobox"');
  });

  it("draws the tab the URL names", () => {
    expect(page({ tab: "timeline" })).toContain("What happened?");
    expect(page({ tab: "overview" })).not.toContain("What happened?");
  });

  it("gives a lead three tabs, and opens Overview for its Related link", () => {
    const lead = { id: "l1", lead_name: "Bosch", organization_name: "Bosch India", owner_email: "priya@fracktal.in" };
    const html = page({ entity: "leads", record: lead, tab: "related" });
    expect(tabs(html)).toEqual(["Overview", "Timeline", "Fields"]);
    expect(html).toMatch(/aria-selected="true"[^>]*>[\s\S]*?Overview/);
    expect(html).toContain("Bosch India");
  });

  it("shows a contact its title, its company, its email and its phone", () => {
    const contact = {
      id: "c1",
      first_name: "Anitha",
      title: "Buyer",
      email: "anitha@bosch.in",
      phone: "+91 80 4123 4567",
    };
    const html = page({ entity: "contacts", record: contact, companyName: "Bosch India" });
    for (const word of ["Buyer", "Bosch India", 'href="mailto:anitha@bosch.in"', 'href="tel:+918041234567"']) {
      expect(html).toContain(word);
    }
  });

  it("shows a company its industry, its phone and its owner", () => {
    const company = { id: "o1", name: "Bosch", industry: "Automotive", phone: "+91 80 4123 4567", owner_email: "priya@fracktal.in" };
    const html = page({ entity: "organizations", record: company });
    expect(html).toContain("Automotive");
    expect(html).toContain('href="tel:+918041234567"');
    expect(html).toContain("priya@fracktal.in");
  });

  it("names the facts of the header in one place", () => {
    expect(summaryFacts("deals", mirroredDeal, null).map((f) => f.label)).toEqual(["Amount", "Owner", "Close"]);
  });
});

describe("the place in the list (CRM-X4 item 5)", () => {
  it("shows 2 of 3 and two arrows when a list is behind the record", () => {
    const html = page({ order: ["d1", "d2", "d3"] });
    expect(html).toContain("2 of 3");
    expect(html).toContain('aria-label="Previous record"');
    expect(html).toContain('aria-label="Next record"');
  });

  it("marks its body, so navStep can tell the sheet from another dialog", () => {
    expect(RECORD_PAGE_ATTR).toBe("data-crm-record-page");
    expect(page({})).toContain(`${RECORD_PAGE_ATTR}=""`);
  });

  it("shows no arrows when no list is behind the record", () => {
    const html = page({ order: null });
    expect(html).not.toContain("Previous record");
    expect(html).not.toContain(" of ");
  });

  it("listens for j and k only through navStep, which checks that no modifier is down", () => {
    const sheet = readFileSync(new URL("./RecordSheet.tsx", import.meta.url), "utf8");
    expect(sheet).toMatch(/navStep\(/);
    const nav = readFileSync(new URL("../lib/recordNav.ts", import.meta.url), "utf8");
    expect(nav).toMatch(/!e\.metaKey && !e\.ctrlKey/);
  });

  it("is fed by the page: the order of the list and a step that replaces the URL", () => {
    const src = readFileSync(new URL("../page.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/order=\{/);
    // `replaceView`, not `router.replace`: the router waits on a server
    // request (lib/history.ts).
    expect(src).toMatch(/replaceView\(viewHref\(openRecord\(/);
  });
});

describe("the timeline tab (CRM-X4 items 3 and 4)", () => {
  const overdue: TimelineEntry = {
    kind: "activity",
    origin: "own",
    activity: { id: "t1", type: "task", subject: "Send the quote", due_at: "2026-10-01T00:00:00.000Z" },
  };

  it("draws the filter and the Overdue chip", () => {
    const html = page({ tab: "timeline", timeline: [overdue] });
    for (const label of ["All", "Notes", "Calls", "Meetings", "Tasks", "Emails", "Stage changes"]) {
      expect(html).toContain(`>${label}<`);
    }
    expect(html).toContain("Overdue");
    expect(html).toContain("text-destructive");
  });

  it("lists the next open activity on Overview", () => {
    expect(page({ tab: "overview", timeline: [overdue] })).toContain("Send the quote");
  });
});
