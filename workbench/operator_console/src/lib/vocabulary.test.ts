// CP-13b (§6A.14, done-when row 11): the seven UI vocabularies know `decide`.
//
// 🔴 **One case for each map.** Each map was written before the task existed,
// so each one missed it. A map that loses `decide` again reds its own case.

import { describe, expect, it } from "vitest";

import { KIND_LABEL, MODEL_KINDS, type ModelKind } from "./contract";
import { TASK_KIND } from "./fallback";
import { VERBS, verbFitsTask, verbsForTask } from "./invocation";
import {
  PROVIDER_GUIDES,
  ROUTED_TODAY,
  SECTIONS,
  type SectionKey,
  type VendorJob,
  isRoutedToday,
  sectionOf,
} from "./providerGuides";
import { catalogFromWire } from "./read";

describe("the seven vocabularies know decide", () => {
  it("1. VERBS offers native_typesafe", () => {
    expect(VERBS).toContain("native_typesafe");
  });

  it("2. TASK_KIND maps the decide task to the decide kind", () => {
    expect(TASK_KIND.decide).toBe("decide");
  });

  it("3. KIND_FROM_TASK files a decide capability under the decide kind", () => {
    const catalog = catalogFromWire({
      tasks: [{ slug: "decide", label: "Make decisions", natural_unit: "tokens" }],
      capabilities: [{ model: "typesafe/jev-1.13.0", task: "decide" }],
      bindings: [],
      rates: [],
    } as never);
    expect(catalog.models[0].kinds).toEqual(["decide"]);
  });

  it("4. ModelKind and MODEL_KINDS hold decide, with a label", () => {
    const kind: ModelKind = "decide";
    expect(MODEL_KINDS).toContain(kind);
    expect(KIND_LABEL[kind]).toBeTruthy();
  });

  it("5. VendorJob holds decide, and the TypeSafe guide serves it", () => {
    const job: VendorJob = "decide";
    expect(PROVIDER_GUIDES.typesafe.serves).toEqual([job]);
  });

  it("6. ROUTED_TODAY includes decide, so a TypeSafe key reads as called", () => {
    expect(ROUTED_TODAY).toContain("decide");
    expect(isRoutedToday("typesafe")).toBe(true);
  });

  it("7. SectionKey has a decide section, and TypeSafe sits in it", () => {
    const key: SectionKey = "decide";
    expect(SECTIONS.map((s) => s.key)).toContain(key);
    expect(sectionOf("typesafe")).toBe(key);
  });
});

describe("the verb follows the job (mirrors check_invocation_for_task)", () => {
  it("offers only the native verbs for decide", () => {
    expect(verbsForTask("decide")).toEqual(["native_typesafe", "native_aimlapi"]);
  });

  it("never offers a native verb for any other job", () => {
    for (const task of ["chat", "transcribe", "speak", "image", "embed"]) {
      expect(verbsForTask(task)).not.toContain("native_typesafe");
      expect(verbsForTask(task)).not.toContain("native_aimlapi");
      expect(verbsForTask(task).length).toBeGreaterThan(0);
    }
  });

  it("refuses both wrong pairs, in both directions", () => {
    expect(verbFitsTask("acompletion", "decide")).toBe(false);
    expect(verbFitsTask("native_typesafe", "chat")).toBe(false);
    expect(verbFitsTask("native_typesafe", "decide")).toBe(true);
    expect(verbFitsTask("acompletion", "chat")).toBe(true);
  });
});

describe("the TypeSafe guide", () => {
  const g = PROVIDER_GUIDES.typesafe;

  it("says where to get the key", () => {
    expect(g.setupUrl).toBe("https://typesafe.ai");
  });

  it("gives the model, the verb and the profile numbers, with output 0", () => {
    const steps = g.steps.join(" ");
    expect(steps).toContain("typesafe/jev-1.13.0");
    expect(steps).toContain("native_typesafe");
    expect(steps).toContain("0.042");
    expect(steps).toContain("output 0");
    expect(steps).toContain("64000");
  });

  it("says the vendor is not a litellm provider", () => {
    const note = SECTIONS.find((s) => s.key === "decide")?.note ?? "";
    expect(note).toContain("not through litellm");
  });

  it("says plainly why the Console calls TypeSafe natively", () => {
    expect(g.description).toContain(
      "litellm reaches TypeSafe only through its Proxy, which we do not run, " +
        "so the Console calls it natively.",
    );
  });
});

describe("the AI/ML API guide (CP-13h)", () => {
  const g = PROVIDER_GUIDES.aimlapi;

  it("serves decide, sits in the Decisions section, and reads as called", () => {
    expect(g.serves).toEqual(["decide"]);
    expect(sectionOf("aimlapi")).toBe("decide");
    expect(isRoutedToday("aimlapi")).toBe(true);
  });

  it("gives the provider id, the model, the verb and the window", () => {
    const steps = g.steps.join(" ");
    expect(steps).toContain("provider id aimlapi");
    expect(steps).toContain("aimlapi/typesafe/jev");
    expect(steps).toContain("native_aimlapi");
    expect(steps).toContain("no streaming");
    expect(steps).toContain("32000");
    expect(steps).toContain("bills the cost it reports");
    expect(steps).toContain("Try a decision");
  });

  it("the native verb fits decide and not chat", () => {
    expect(verbFitsTask("native_aimlapi", "decide")).toBe(true);
    expect(verbFitsTask("native_aimlapi", "chat")).toBe(false);
  });
});
