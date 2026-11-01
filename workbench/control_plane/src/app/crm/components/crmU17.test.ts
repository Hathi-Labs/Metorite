// CRM-U17a: the three defects, a refused edit keeps its input, no modal on a
// modal, and one set of words (crm_platform.md section 20.21).
//
// The runner has no DOM. A component renders to a string, the store runs on a
// mocked api, and a wiring rule is a scan of the source.
import { readdirSync, readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  listStatuses: vi.fn(),
  listLostReasons: vi.fn(),
}));

import * as api from "../lib/api";
import { typeLabel } from "../lib/fieldStudio";
import { moduleLabel, moduleRailRows, type CrmModule } from "../lib/modules";
import { validateOverlayProbability, validateStatusDraft, vocabularyShows } from "../lib/settings";
import { useCrmStore } from "../lib/store";
import PipelineSettings, { NativeStageForm } from "./PipelineSettings";
import type { Status } from "../lib/types";

// CRLF checkouts read as LF, so a multi-line scan holds on both.
const read = (rel: string) =>
  readFileSync(new URL(rel, import.meta.url), "utf-8").split("\r\n").join("\n");
const noop = async () => true;
const listStatuses = vi.mocked(api.listStatuses);
const listLostReasons = vi.mocked(api.listLostReasons);

const OPEN: Status = {
  id: "s1",
  name: "Qualified",
  color: "gray",
  position: 0,
  type: "open",
  is_default: false,
  probability: 20,
};

/** The settings page with empty lists and a given state of the first read. */
const settings = (state: "loading" | "loaded" | "failed", error: string | null = null) =>
  renderToStaticMarkup(
    createElement(PipelineSettings, {
      dealStatuses: [],
      leadStatuses: [],
      lostReasons: [],
      vocabularyState: state,
      vocabularyError: error,
      onRetryVocabulary: () => undefined,
      saving: false,
      onSaveStatus: noop,
      onRemoveStatus: noop,
      onSaveLostReason: noop,
      onRemoveLostReason: noop,
      onReorderLostReasons: async () => undefined,
    }),
  );

// ── 1. A Native module shows in the rail ───────────────────────────────────

describe("the rail in Native mode (done-when 1)", () => {
  it("reads the modules in both modes", () => {
    const page = read("../page.tsx");
    expect(page).toContain("useModules(true)");
    expect(page).not.toContain('useModules(ctx.mode === "mirror")');
  });

  it("lists a Native module that holds records, with its label", () => {
    const modules: CrmModule[] = [
      { api_name: "Service_Requests", label: "Service requests", singular_label: null, count: 4 },
    ];
    expect(moduleRailRows(modules)).toMatchObject([{ module: "Service_Requests", label: "Service requests", count: 4 }]);
  });

  it("says in the gateway docstring that a Native org lists the modules that hold records", () => {
    const route = read("../../../../../../apps/services/gateway/gateway/routes/crm/modules.py");
    expect(route).not.toContain("A Native org, and an org with no profile, gets an empty list.");
  });
});

// ── 2. No raw module name ──────────────────────────────────────────────────

describe("no raw API name (done-when 2)", () => {
  it("moduleLabel falls back to a readable name", () => {
    expect(moduleLabel([], "service_requests")).toBe("Service requests");
    expect(moduleLabel(null, "Service_Requests")).toBe("Service Requests");
    expect(moduleLabel([], null)).toBe("Module");
  });

  it("a rail row with no label reads as a name", () => {
    const rows = moduleRailRows([{ api_name: "service_requests", label: "", singular_label: null, count: 1 }]);
    expect(rows[0].label).toBe("Service requests");
  });

  it("the record sheet and the type word fall back to a readable name", () => {
    expect(read("./ModuleRecordSheet.tsx")).toContain("page?.module.label || humanizeKey(module)");
    expect(typeLabel("textarea")).toBe("Long text");
    expect(typeLabel("rich_text_area")).toBe("Rich text area");
  });

  it("the Field map cards show the type word", () => {
    expect(read("./FieldMapCard.tsx")).toContain("{typeLabel(field.type)}");
    expect(read("./ConvertMappingCard.tsx")).toContain("{typeLabel(field.type)}");
  });
});

// ── 3. Loading is not empty ────────────────────────────────────────────────

describe("the vocabulary read (done-when 3)", () => {
  beforeEach(() => {
    useCrmStore.setState({
      dealStatuses: [],
      leadStatuses: [],
      lostReasons: [],
      vocabularyState: "loading",
      vocabularyError: null,
      error: null,
    });
  });

  it("an empty list is loading until a read says it is empty", () => {
    expect(vocabularyShows("loading", 0)).toBe("loading");
    expect(vocabularyShows("failed", 0)).toBe("failed");
    expect(vocabularyShows("loaded", 0)).toBe("empty");
    expect(vocabularyShows("loading", 3)).toBe("rows");
    expect(vocabularyShows("failed", 3)).toBe("rows");
  });

  it("the store starts in loading and lands in loaded", async () => {
    expect(useCrmStore.getState().vocabularyState).toBe("loading");
    listStatuses.mockResolvedValue([OPEN]);
    listLostReasons.mockResolvedValue([]);
    await useCrmStore.getState().loadVocabulary();
    const state = useCrmStore.getState();
    expect(state.vocabularyState).toBe("loaded");
    expect(state.dealStatuses).toEqual([OPEN]);
    expect(state.lostReasons).toEqual([]);
  });

  it("a failed read keeps its reason, and the shapes of the lists stay", async () => {
    listStatuses.mockRejectedValue(new Error("The CRM did not answer."));
    listLostReasons.mockResolvedValue([]);
    await useCrmStore.getState().loadVocabulary();
    const state = useCrmStore.getState();
    expect(state.vocabularyState).toBe("failed");
    expect(state.vocabularyError).toBe("The CRM did not answer.");
    expect(state.dealStatuses).toEqual([]);
    expect(state.leadStatuses).toEqual([]);
  });

  it("the stages page shows a loading row and never 'No stages yet' while it reads", () => {
    const html = settings("loading");
    expect(html).toContain("Loading…");
    expect(html).not.toContain("No stages yet");
    expect(html).not.toContain("No statuses yet");
    expect(html).not.toContain("No lost reasons yet");
  });

  it("a failed read shows its reason and Retry", () => {
    const html = settings("failed", "The CRM did not answer.");
    expect(html).toContain("The CRM did not answer.");
    expect(html).toContain(">Retry<");
    expect(html).not.toContain("No stages yet");
  });

  it("an empty answer shows the empty lines", () => {
    const html = settings("loaded");
    expect(html).toContain("No stages yet. Add the first one.");
    expect(html).toContain("No statuses yet. Add the first one.");
    expect(html).not.toContain("Loading…");
  });

  it("the loading, failed and empty rows share one height", () => {
    const cards = read("./SettingsCards.tsx");
    expect(cards).toContain("export const STATE_ROW");
    const list = read("./PipelineSettings.tsx");
    expect(list).toContain("<li className={STATE_ROW}>{words.empty[mode]}</li>");
    expect(cards).toContain("<p className={STATE_ROW} role=\"status\">");
    expect(cards).toContain("<div className={STATE_ROW} role=\"alert\">");
  });

  afterEach(() => vi.clearAllMocks());
});

// ── 4. A refused edit keeps its input ──────────────────────────────────────

describe("a refused stage edit (done-when 4)", () => {
  const form = (status: Status, refused: boolean) =>
    renderToStaticMarkup(
      createElement(NativeStageForm, {
        kind: "deal",
        status,
        saving: false,
        refused,
        onSave: noop,
        onAdd: noop,
        onRemove: () => undefined,
        onCancel: () => undefined,
      }),
    );

  // `renderToStaticMarkup` reads the initial state of the store (as crmC1 does).
  const initial = useCrmStore.getInitialState();
  afterEach(() => {
    initial.settingsDrafts = {};
    initial.error = null;
  });

  it("keeps the typed value when the re-read brings a new object, and shows the reason", () => {
    initial.settingsDrafts = {
      [`status:deal:${OPEN.id}`]: { base: OPEN, value: { ...OPEN, name: "Typed draft" } },
    };
    initial.error = "A stage with this name exists.";
    // The re-read of a refused save: the same stage, a new object.
    const served = { ...OPEN };
    const kept = form(served, true);
    expect(kept).toContain('value="Typed draft"');
    expect(kept).toContain("A stage with this name exists.");
    // A re-read with no refusal still drops the draft (the server is the truth).
    expect(form(served, false)).not.toContain('value="Typed draft"');
  });

  it("the hook rebases a kept draft onto the new row, and the reason has a fallback", () => {
    const source = read("./PipelineSettings.tsx");
    expect(source).toContain("if (refused && stored && stored.base !== served) setDraft(key, { base: served, value: stored.value });");
    expect(source).toContain("storeError ?? TRY_AGAIN");
  });

  it("the post-write re-read stays (workbench/AGENTS.md)", () => {
    const store = read("../lib/store.ts");
    const at = store.indexOf("async saveStatus(");
    expect(store.slice(at, at + 700)).toContain("await get().loadVocabulary();");
  });
});

// ── 5. No modal on a modal, no toast over a modal ──────────────────────────

describe("one layer at a time (done-when 5)", () => {
  it("the stage Edit dialog holds no ConfirmDialog", () => {
    const source = read("./PipelineSettings.tsx");
    const from = source.indexOf("export function NativeStageForm");
    const to = source.indexOf("export function MirrorStageForm");
    expect(from).toBeGreaterThan(0);
    expect(to).toBeGreaterThan(from);
    expect(source.slice(from, to)).not.toContain("ConfirmDialog");
  });

  it("a stage delete closes the Edit dialog, then asks", () => {
    const source = read("./PipelineSettings.tsx");
    expect(source).toContain("setEditing(null);\n          setConfirmingDelete(statusId);");
    // A cancel goes back to the dialog, and a refusal opens it again with the reason.
    expect(source).toContain("setEditing(confirmingDelete);");
    expect(source).toContain("setDeleteRefused(true);");
  });

  it("the field dialog closes before the retire confirm opens", () => {
    const studio = read("./FieldStudio.tsx");
    expect(studio).toContain('setConfirm({ kind: "retire", def, back: fieldForm });\n          setFieldForm(null);');
    expect(studio).toContain("if (confirm?.kind === \"retire\") setFieldForm(confirm.back);");
    expect(read("./FieldStudioFieldDialog.tsx")).not.toContain("ConfirmDialog");
  });

  it("the sum picker and the suggestion preview close before a toast", () => {
    const questions = read("./OpenQuestions.tsx");
    for (const fn of ["async function answer(", "async function correctTo("]) {
      const at = questions.indexOf(fn);
      const body = questions.slice(at, questions.indexOf("\n  }\n", at));
      expect(body.indexOf("closeFirst?.();"), fn).toBeGreaterThan(0);
      expect(body.indexOf("closeFirst?.();"), fn).toBeLessThan(body.indexOf("toast.show("));
      expect(body.indexOf("closeFirst?.();"), fn).toBeLessThan(body.indexOf("fail("));
    }
    expect(questions).toContain("apply(previewing, () => setPreview(null))");
    const suggestions = read("./Suggestions.tsx");
    const at = suggestions.indexOf("async function apply(");
    const apply = suggestions.slice(at, suggestions.indexOf("\n  }\n", at));
    expect(apply.indexOf("closeFirst?.();")).toBeLessThan(apply.indexOf("toast.show("));
    expect(apply.lastIndexOf("closeFirst?.();")).toBeLessThan(apply.lastIndexOf("toast.show("));
    expect(suggestions).toContain("actions.apply(suggestion, () => setOpen(false))");
    expect(suggestions).toContain("actions.apply(s, () => setPreviewingSlot(null))");
  });
});

// ── 6. One set of words ────────────────────────────────────────────────────

describe("one set of words (done-when 6)", () => {
  /** Every source file of the CRM page that is not a test. */
  function sources(dir = new URL("../", import.meta.url)): { name: string; text: string }[] {
    const out: { name: string; text: string }[] = [];
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const next = new URL(entry.name + (entry.isDirectory() ? "/" : ""), dir);
      if (entry.isDirectory()) out.push(...sources(next));
      else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\./.test(entry.name)) {
        out.push({ name: entry.name, text: readFileSync(next, "utf-8") });
      }
    }
    return out;
  }

  const OLD_WORDS = [
    "Show this column on the board",
    "The server refuses",
    "the order shown is the server's",
    "not set up on this server",
    "The server restarted",
    "the server restarted",
    "Gateway error ${",
    'label: "Probability"',
    "Set probability to",
    "A stage probability",
    "A probability is a whole",
    "'max_dwell_days' is",
  ];

  it("no old word is left in a string that a user reads", () => {
    const all = sources();
    expect(all.length).toBeGreaterThan(50);
    for (const word of OLD_WORDS) {
      const hits = all.filter((file) => file.text.includes(word)).map((file) => file.name);
      expect(hits, word).toEqual([]);
    }
  });

  it("the gateway label of the system field reads Win chance", () => {
    const core = read("../../../../../../apps/services/gateway/gateway/routes/crm/core.py");
    expect(core).toContain('_sf("probability", "Win chance", "percent")');
    expect(core).not.toContain('_sf("probability", "Probability"');
  });

  it("Win chance is the word of the deal field", () => {
    expect(read("./FieldsPanel.tsx")).toContain('label: "Win chance"');
  });

  it("the stage box reads Hide on the board, and a hidden row reads Hidden", () => {
    const list = read("./PipelineSettings.tsx");
    expect(list).toContain("Hide on the board");
    expect(list).toContain("A hidden stage still shows while it holds a deal.");
    expect(list).toContain("checked={hidden}");
    expect(read("./FieldsCard.tsx")).not.toMatch(/^\s*hidden\s*$/m);
  });

  it("a refusal with no detail says one sentence, with no status code", () => {
    const api = read("../lib/api.ts");
    expect(api).toContain('export const TRY_AGAIN = "That did not work. Try again.";');
    expect(api).toContain("detailOf(body) || TRY_AGAIN");
  });

  it("a rule message names a field by its label, and a type by its word", () => {
    const base = { name: "Qualified", type: "open" as const, probability: 10 };
    expect(validateStatusDraft({ ...base, max_dwell_days: 0 }, "deal")).not.toContain("max_dwell_days");
    expect(validateStatusDraft({ ...base, required_fields: ["close_date_x"] }, "deal")).not.toMatch(/[[\]"_]/);
    expect(validateStatusDraft({ ...base, type: "won", probability: 5 }, "deal")).toContain("A Won stage");
    expect(validateStatusDraft({ ...base, probability: 400 }, "deal")).toContain("A win chance is a percentage");
    expect(validateOverlayProbability(400)).toContain("win chance");
  });

  it("the stage pill shows Won and Lost, not the type id", () => {
    expect(read("./StatusPill.tsx")).toContain("{STATUS_TYPE_LABELS[option.type]}");
  });

  it("an unknown error code in a message reads without the word server", () => {
    const sync = read("../lib/syncHealth.ts");
    expect(sync).not.toMatch(/"[^"\n]*\bserver\b[^"\n]*"/);
    expect(read("../lib/cutoverSwitch.ts")).not.toMatch(/"[^"\n]*\bserver\b[^"\n]*"/);
  });
});
