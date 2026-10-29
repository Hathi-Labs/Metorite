// CRM-U16: a deal gets its company and contacts (crm_platform.md section 20.20).
//
// The runner has no DOM. A component renders to a string, the store runs on a
// mocked api, and a wiring rule is a scan of the source.
import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", () => ({
  CrmError: class CrmError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  listRecords: vi.fn(),
  createRecord: vi.fn(),
  addDealContact: vi.fn(),
  listDealContacts: vi.fn(),
  getPipeline: vi.fn(),
  listRecordsPage: vi.fn(),
}));

import * as api from "../lib/api";
import { CrmModeContext } from "../lib/modeContext";
import { linkFields, pickerNoun, searchHits } from "../lib/recordPicker";
import { summaryFacts } from "../lib/fields";
import { useCrmStore } from "../lib/store";
import FieldsPanel, { AddContact } from "./FieldsPanel";
import RecordPicker from "./RecordPicker";
import { QuickCreateForm } from "./QuickCreateModal";

const noop = () => undefined;
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf-8");
const listRecords = vi.mocked(api.listRecords);
const createRecord = vi.mocked(api.createRecord);
const addDealContact = vi.mocked(api.addDealContact);
const listDealContacts = vi.mocked(api.listDealContacts);

const page = (rows: Record<string, unknown>[]) => ({ rows, total: rows.length }) as never;
const person = (id: string, first: string, last = "") => ({ id, first_name: first, last_name: last });

beforeEach(() => {
  vi.clearAllMocks();
  useCrmStore.setState({ error: null, refusal: null, saving: false, dealContacts: [] });
});

// ── The picker search ──────────────────────────────────────────────────────

describe("the picker search", () => {
  it("finds nothing for an empty search with no company", async () => {
    expect(await searchHits("contacts", "  ")).toEqual([]);
    expect(await searchHits("organizations", "")).toEqual([]);
    expect(listRecords).not.toHaveBeenCalled();
  });

  it("searches companies by name on the list route", async () => {
    listRecords.mockResolvedValueOnce(page([{ id: "o1", name: "Bosch" }]));
    expect(await searchHits("organizations", "bos")).toEqual([{ id: "o1", name: "Bosch" }]);
    expect(listRecords).toHaveBeenCalledWith("organizations", { q: "bos", page_size: 10 });
  });

  it("lists the contacts of the chosen company first, with no search typed", async () => {
    listRecords.mockResolvedValueOnce(page([person("c1", "Ann", "Roy")]));
    const hits = await searchHits("contacts", "", "o1");
    expect(hits).toEqual([{ id: "c1", name: "Ann Roy" }]);
    expect(listRecords).toHaveBeenCalledTimes(1);
    expect(listRecords).toHaveBeenCalledWith("contacts", { company_id: "o1", page_size: 10 });
  });

  it("puts the company's matches before the others, and any contact stays pickable", async () => {
    listRecords
      .mockResolvedValueOnce(page([person("c2", "Anil")]))
      .mockResolvedValueOnce(page([person("c9", "Anna"), person("c2", "Anil")]));
    const hits = await searchHits("contacts", "an", "o1");
    expect(hits.map((h) => h.id)).toEqual(["c2", "c9"]);
    expect(listRecords).toHaveBeenNthCalledWith(1, "contacts", { q: "an", company_id: "o1", page_size: 10 });
    expect(listRecords).toHaveBeenNthCalledWith(2, "contacts", { q: "an", page_size: 10 });
  });

  it("does not narrow a company search", async () => {
    listRecords.mockResolvedValueOnce(page([]));
    await searchHits("organizations", "x", "o1");
    expect(listRecords).toHaveBeenCalledWith("organizations", { q: "x", page_size: 10 });
  });
});

describe("the words and the link fields", () => {
  it("uses the CRM's own names for the entities", () => {
    expect(pickerNoun("organizations", null)).toBe("Company");
    expect(pickerNoun("contacts", null)).toBe("Contact");
    const labels = { organizations: { label: "Accounts", singular_label: "Account" } };
    expect(pickerNoun("organizations", labels)).toBe("Account");
  });

  it("sends the company of a deal and of a contact, and the contact of a deal", () => {
    const c = { id: "o1", name: "Bosch" };
    const p = { id: "c1", name: "Ann" };
    expect(linkFields("deals", c, p)).toEqual({ company_id: "o1", contact_id: "c1" });
    expect(linkFields("deals", null, p)).toEqual({ contact_id: "c1" });
    expect(linkFields("contacts", c, p)).toEqual({ company_id: "o1" });
    expect(linkFields("leads", c, p)).toEqual({});
    expect(linkFields("organizations", c, p)).toEqual({});
    expect(linkFields("deals", null, null)).toEqual({});
  });
});

// ── The forms ──────────────────────────────────────────────────────────────

const form = (entity: "deals" | "contacts" | "leads", mode: "mirror" | "native" | null = "native") =>
  renderToStaticMarkup(
    createElement(
      CrmModeContext.Provider,
      { value: mode },
      createElement(QuickCreateForm, { entity, saving: false, onClose: noop, onCreate: noop }),
    ),
  );

describe("the create forms", () => {
  it("gives a new deal a Company and a Contact picker", () => {
    const html = form("deals");
    expect(html).toContain('aria-label="Search for company"');
    expect(html).toContain('aria-label="Search for contact"');
  });

  it("gives a new contact a Company picker and no Contact picker", () => {
    const html = form("contacts");
    expect(html).toContain('aria-label="Search for company"');
    expect(html).not.toContain('aria-label="Search for contact"');
  });

  it("gives a lead neither", () => {
    expect(form("leads")).not.toContain("Search for");
  });

  it("offers no picker in Mirror mode", () => {
    expect(form("deals", "mirror")).not.toContain("Search for");
  });

  it("adds the picks to the body that the form saves, in Native only", () => {
    const src = read("./QuickCreateModal.tsx");
    expect(src).toContain("linkFields(entity, takesCompany ? company : null, takesContact ? contact : null)");
    expect(src).toContain('const takesCompany = !locked && (entity === "deals" || entity === "contacts")');
    expect(src).toContain('const takesContact = !locked && entity === "deals"');
  });

  it("narrows the contact picker to the chosen company", () => {
    const src = read("./QuickCreateModal.tsx");
    expect(src).toContain("companyId={company?.id ?? null}");
  });
});

// ── The store ──────────────────────────────────────────────────────────────

describe("the store saves a deal with its company and contact", () => {
  it("creates the deal with company_id, then attaches the contact as primary", async () => {
    createRecord.mockResolvedValueOnce({ id: "d1" });
    addDealContact.mockResolvedValueOnce({} as never);
    listRecords.mockResolvedValue(page([]));
    const id = await useCrmStore
      .getState()
      .createRecord("deals", { name: "Bosch", company_id: "o1", contact_id: "c1" });
    expect(id).toBe("d1");
    // The contact is no column of the deal, so it never reaches the create.
    expect(createRecord).toHaveBeenCalledWith("deals", { name: "Bosch", company_id: "o1" });
    expect(addDealContact).toHaveBeenCalledWith("d1", { contact_id: "c1", is_primary: true });
  });

  it("attaches nothing when no contact is picked", async () => {
    createRecord.mockResolvedValueOnce({ id: "d1" });
    listRecords.mockResolvedValue(page([]));
    await useCrmStore.getState().createRecord("deals", { name: "Bosch", company_id: "o1" });
    expect(addDealContact).not.toHaveBeenCalled();
  });

  it("keeps the deal and says so when the contact cannot be attached", async () => {
    createRecord.mockResolvedValueOnce({ id: "d1" });
    addDealContact.mockRejectedValueOnce(new Error("Contact not found"));
    listRecords.mockResolvedValue(page([]));
    const id = await useCrmStore.getState().createRecord("deals", { name: "B", contact_id: "c1" });
    expect(id).toBe("d1");
    expect(useCrmStore.getState().error).toContain("The deal was saved, but its contact was not added.");
  });

  it("sends a contact's company_id unchanged", async () => {
    createRecord.mockResolvedValueOnce({ id: "c1" });
    listRecords.mockResolvedValue(page([]));
    await useCrmStore.getState().createRecord("contacts", { first_name: "Ann", company_id: "o1" });
    expect(createRecord).toHaveBeenCalledWith("contacts", { first_name: "Ann", company_id: "o1" });
  });

  it("attaches the first contact of a deal as primary", async () => {
    listDealContacts.mockResolvedValue({ rows: [] } as never);
    await useCrmStore.getState().attachContact("d1", "c1");
    expect(addDealContact).toHaveBeenCalledWith("d1", { contact_id: "c1", is_primary: true });
  });

  it("attaches a later contact without taking the star", async () => {
    listDealContacts.mockResolvedValue({
      rows: [{ contact: { id: "c0" }, is_primary: true }],
    } as never);
    await useCrmStore.getState().attachContact("d1", "c1");
    expect(addDealContact).toHaveBeenCalledWith("d1", { contact_id: "c1", is_primary: false });
  });

  it("leaves a contact that is on the deal as it is", async () => {
    listDealContacts.mockResolvedValue({
      rows: [{ contact: { id: "c1" }, is_primary: true }],
    } as never);
    await useCrmStore.getState().attachContact("d1", "c1");
    expect(addDealContact).not.toHaveBeenCalled();
  });
});

// ── The deal page ──────────────────────────────────────────────────────────

const deal = { id: "d1", name: "Bosch", company_id: "o1", ext_source: null as string | null };

const overview = (record: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  renderToStaticMarkup(
    createElement(FieldsPanel, {
      part: "overview",
      entity: "deals",
      record,
      statuses: [],
      dealContacts: [],
      saving: false,
      onPatch: noop,
      onConvert: noop,
      onSetPrimary: noop,
      onDetach: noop,
      onOpenContact: noop,
      companyName: "Bosch India",
      ...extra,
    }),
  );

const related = (record: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  overview(record, { part: "related", onAttach: noop, ...extra });

describe("the Company of a deal", () => {
  it("shows the company name, with a pencil, in Native", () => {
    const html = overview(deal);
    expect(html).toContain("Company");
    expect(html).toContain("Bosch India");
    expect(html).toContain("lucide-pencil");
  });

  it("shows a dash for a deal with no company", () => {
    const html = overview({ ...deal, company_id: null });
    expect(html).not.toContain("Bosch India");
  });

  it("reads the company in Mirror as a locked value, with no picker", () => {
    const html = overview({ ...deal, ext_source: "zoho" });
    expect(html).toContain("Bosch India");
    expect(html).toContain("Edit this in Zoho.");
    expect(html).not.toContain("Search for company");
  });

  it("uses the CRM's own name for a company", () => {
    const labels = { organizations: { label: "Accounts", singular_label: "Account" } };
    const html = overview(deal, { labels });
    expect(html).toMatch(/>Account<\/dt>/);
  });

  it("opens the picker on the pencil and saves company_id on a pick", () => {
    const src = read("./FieldsPanel.tsx");
    expect(src).toContain("editing && field.lookup");
    expect(src).toContain("onPatch({ [field.key]: next ? next.id : null })");
  });

  it("is a fact of the summary header", () => {
    expect(summaryFacts("deals", deal, "Bosch India").some((f) => f.key === "company")).toBe(true);
    expect(summaryFacts("deals", { ...deal, company_id: null }, null).some((f) => f.key === "company")).toBe(false);
  });

  it("reads the deal's company name on the record page, as a contact does", () => {
    expect(read("./RecordSheet.tsx")).toContain('(entity === "contacts" || entity === "deals")');
  });
});

describe("+ Add contact on the Related tab", () => {
  it("shows the button in Native, with the star and remove kept", () => {
    const html = related(deal, {
      dealContacts: [{ contact: { id: "c1", first_name: "Ann" }, role: null, is_primary: true }],
    });
    expect(html).toContain("Add contact");
    expect(html).toContain("Primary contact");
    expect(html).toContain("Remove from this deal");
  });

  it("is absent in Mirror", () => {
    expect(related({ ...deal, ext_source: "zoho" })).not.toContain("Add contact");
  });

  it("is absent where the page gives no attach action", () => {
    expect(related(deal, { onAttach: undefined })).not.toContain("Add contact");
  });

  it("names the button with the CRM's word for a contact", () => {
    const labels = { contacts: { label: "People", singular_label: "Person" } };
    expect(related(deal, { labels })).toContain("Add person");
  });

  it("closed, it is one button", () => {
    const html = renderToStaticMarkup(
      createElement(AddContact, {
        label: "Contact",
        companyId: "o1",
        companyName: "Bosch",
        attached: [],
        disabled: false,
        onAttach: noop,
      }),
    );
    expect(html).toMatch(/<button[^>]*>[\s\S]*Add contact<\/button>/);
  });

  it("wires the attach to the store, for a deal only", () => {
    const src = read("./RecordSheet.tsx");
    expect(src).toContain("attachContact(record.id as string, contactId)");
    expect(src).toContain('entity === "deals"');
  });
});

// ── The picker control ─────────────────────────────────────────────────────

describe("the picker is keyboard-reachable and built from the shared controls", () => {
  const html = renderToStaticMarkup(
    createElement(RecordPicker, { target: "organizations", label: "Company", value: null, onChange: noop }),
  );

  it("draws a search box and a button, both in the tab order", () => {
    expect(html).toMatch(/<input[^>]*aria-label="Search for company"/);
    expect(html).toMatch(/<button[^>]*>/);
    expect(html).not.toContain('tabindex="-1"');
    expect(html).not.toContain("<select");
  });

  it("is built from Input and SelectButton, with no raw control or click-only element", () => {
    const src = read("./RecordPicker.tsx");
    expect(src).toContain('from "@/components/ui/Input"');
    expect(src).toContain('from "@/components/ui/SelectButton"');
    expect(src).not.toMatch(/<(input|select|textarea)\b/);
    expect(src).not.toMatch(/<div[^>]*onClick/);
  });

  it("lists a company's contacts at once, and waits for the keys of a typed search", () => {
    const src = read("./RecordPicker.tsx");
    expect(src).toContain("q ? LOOKUP_WAIT_MS : 0");
  });

  it("shows the hint when a company narrows the contacts", () => {
    const withCompany = renderToStaticMarkup(
      createElement(RecordPicker, {
        target: "contacts",
        label: "Contact",
        value: null,
        onChange: noop,
        companyId: "o1",
        companyName: "Bosch",
      }),
    );
    expect(withCompany).toContain("Contacts at Bosch come first.");
    expect(
      renderToStaticMarkup(
        createElement(RecordPicker, { target: "contacts", label: "Contact", value: null, onChange: noop }),
      ),
    ).not.toContain("come first");
  });
});
