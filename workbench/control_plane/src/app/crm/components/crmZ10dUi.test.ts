// CRM-Z10d (UI): a generic module record edits in place in Native mode, and
// reads only in Mirror mode and while the mode is unknown. The runner has no
// DOM, so the sheet renders to a string, and the write goes through a fake
// fetch.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../lib/api";
import { CrmError } from "../lib/api";
import type { CrmMode } from "../lib/connect";
import { CrmModeContext } from "../lib/modeContext";
import {
  NAME_DEF,
  modulePatch,
  ownerDef,
  type ModuleRecord,
  type ModuleRecordPage,
} from "../lib/modules";
import ModuleEditRow, { rowError } from "./ModuleEditRow";
import { ModuleRecordBody, type ModuleEdit } from "./ModuleRecordSheet";
import FieldInput from "./FieldInput";

const noop = () => undefined;

const RECORD: ModuleRecord = {
  id: "11111111-1111-4111-8111-111111111111",
  module: "Install_Base",
  name: "Bosch line 4",
  owner_email: "priya@fracktal.in",
  fields: { Qty: 3, Notes: "old" },
  ext_source: "zoho",
  ext_id: "9001",
  ext_url: null,
  ext_created_at: "2026-10-12T08:00:00Z",
  ext_modified_at: "2026-10-20T08:00:00Z",
};

const def = (key: string, type: string, extra: Record<string, unknown> = {}) => ({
  key,
  label: key,
  type,
  options: null,
  position: 1,
  visible: true,
  source: "zoho" as const,
  api_name: key,
  lookup_module: null,
  ...extra,
});

const PAGE: ModuleRecordPage = {
  module: { api_name: "Install_Base", label: "Install Base", singular_label: "Install", count: 1 },
  record: RECORD,
  field_defs: [def("Qty", "number"), def("Notes", "text")],
  links: {},
};

const owners = [
  { email: "priya@fracktal.in", name: "Priya", member: true },
  { email: "sam@fracktal.in", name: "Sam", member: true },
];

function draw(mode: CrmMode, withEdit = true, record: Partial<ModuleRecord> = {}): string {
  const edit: ModuleEdit = { saving: false, refusal: null, owners, onPatch: async () => true };
  const page = { ...PAGE, record: { ...RECORD, ...record } };
  return renderToStaticMarkup(
    createElement(
      CrmModeContext.Provider,
      { value: mode },
      createElement(ModuleRecordBody, {
        page,
        tab: "overview",
        onTab: noop,
        onOpenLink: noop,
        edit: withEdit ? edit : undefined,
      }),
    ),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("CRM-Z10d UI: the lock follows the mode", () => {
  it("Mirror mode: no editable control", () => {
    const html = draw("mirror");
    expect(html).not.toContain("Edit name");
    expect(html).not.toContain("Edit qty");
    expect(html).not.toMatch(/aria-label="Edit /);
    expect(html).not.toContain("<input");
    expect(html).toContain("priya@fracktal.in");
  });

  it("Mirror mode sends no PATCH", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    draw("mirror");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("an unknown mode (null) stays locked", () => {
    const html = draw(null);
    expect(html).not.toMatch(/aria-label="Edit /);
  });

  it("with no write path the page reads only, even in Native mode", () => {
    expect(draw("native", false)).not.toMatch(/aria-label="Edit /);
  });

  it("Native mode: the name, the owner and each field have an edit control", () => {
    const html = draw("native");
    expect(html).toContain('aria-label="Edit name"');
    expect(html).toContain('aria-label="Edit owner"');
    expect(html).toContain('aria-label="Edit qty"');
    expect(html).toContain('aria-label="Edit notes"');
  });

  it("shows the imported line in Native mode only", () => {
    expect(draw("native")).toContain("From Zoho, created there on 12 Oct 2026");
    expect(draw("mirror")).not.toContain("From Zoho, created there");
    expect(draw(null)).not.toContain("From Zoho, created there");
  });
});

describe("CRM-Z10d UI: the save", () => {
  it("sends only the changed key, and a cleared field sends null", () => {
    expect(modulePatch(NAME_DEF, RECORD, "Bosch line 5")).toEqual({ name: "Bosch line 5" });
    expect(modulePatch(NAME_DEF, RECORD, "Bosch line 4")).toBeNull();
    expect(modulePatch(def("Qty", "number"), RECORD, 4)).toEqual({ fields: { Qty: 4 } });
    expect(modulePatch(def("Notes", "text"), RECORD, "")).toEqual({ fields: { Notes: null } });
    expect(modulePatch(ownerDef(owners), RECORD, "sam@fracktal.in")).toEqual({
      owner_email: "sam@fracktal.in",
    });
    expect(modulePatch(ownerDef(owners), RECORD, null)).toEqual({ owner_email: null });
  });

  it("PATCHes /api/crm/modules/<Module>/records/<id> with that body", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(PAGE), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await api.patchModuleRecord("Install_Base", RECORD.id, { fields: { Qty: 4, Notes: null } });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`/api/crm/modules/Install_Base/records/${RECORD.id}`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ fields: { Qty: 4, Notes: null } });
  });

  it("a 422 field_value keeps its field, and shows beside that field", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({ detail: { code: "field_value", message: "Qty must be a number.", field: "Qty" } }),
            { status: 422 },
          ),
      ),
    );
    const err = await api.patchModuleRecord("Install_Base", RECORD.id, { fields: { Qty: "x" } }).catch((e) => e);
    expect(err).toBeInstanceOf(CrmError);
    expect(err.status).toBe(422);
    expect(err.code).toBe("field_value");
    expect(err.field).toBe("Qty");
    const refusal = { message: err.message, field: err.field as string };
    // The row of Qty shows it. The row of Notes does not.
    expect(rowError("Qty", null, true, false, refusal)).toBe("Qty must be a number.");
    expect(rowError("Notes", null, true, false, refusal)).toBeNull();
    const html = renderToStaticMarkup(
      createElement(FieldInput, { def: def("Qty", "number"), value: "x", onChange: noop, error: refusal.message }),
    );
    expect(html).toContain("Qty must be a number.");
    expect(html).toContain('role="alert"');
  });

  it("a 409 mirrored_record carries its code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: { code: "mirrored_record", message: "Zoho owns this." } }), {
            status: 409,
          }),
      ),
    );
    const err = await api.patchModuleRecord("Install_Base", RECORD.id, { name: "x" }).catch((e) => e);
    expect(err.status).toBe(409);
    expect(err.code).toBe("mirrored_record");
  });

  it("an edit row draws its Pencil and no input until edit starts", () => {
    const html = renderToStaticMarkup(
      createElement(ModuleEditRow, {
        def: NAME_DEF,
        record: RECORD,
        saving: false,
        refusal: null,
        onPatch: async () => true,
      }),
    );
    expect(html).toContain("Edit name");
    expect(html).not.toContain("<input");
  });
});
