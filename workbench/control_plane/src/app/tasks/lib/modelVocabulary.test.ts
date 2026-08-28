// D32.7 — the customer never sees a model. Closes H-72 part one.
//
// ⚠️ **This was live, not theoretical.** `/tasks` is one of the nine live
// panes, `page.tsx` renders `<TaskSettingsModal />` twice, and the modal
// offered every enabled model by raw id under an optgroup labelled "Your
// enabled models". The chosen value reached `AssistantRail`'s chat call.
//
// 🔴 **The break was silent until the flag flip.** The defaults are tiers, so
// nothing looked wrong — but a customer who deliberately picked a model stored
// a bare model id, and the Console refuses one with a 400 rather than coercing
// it. That customer loses their Tasks AI the day `ROUTER_SERVING_ENABLED`
// flips, and they are the customer most engaged with the product.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const MODAL = readFileSync(
  join(__dirname, "..", "components", "TaskSettingsModal.tsx"),
  "utf8",
);

describe("the Tasks settings picker offers tiers only", () => {
  it("has no model optgroup", () => {
    // ⚠️ The comment that REPLACED the optgroup must not quote the label
    // either — this fence went red on its own explanation first, which
    // is the fifth time in this repo that a source check matched the
    // prose forbidding the thing. Text fences are cheap and this one is
    // right for JSX, but the prose has to stay out of its way.
    expect(MODAL).not.toContain("Your enabled models");
    expect(MODAL).not.toContain("enabledModels");
  });

  it("does not read the enabled-model catalogue at all", () => {
    // ⚠️ A read kept alive for a surface that no longer exists is how the
    // surface quietly comes back. The endpoint still serves the operator
    // console and the `preview` email app; this component must not use it.
    expect(MODAL).not.toContain("/api/settings/llm/enabled-models");
  });

  it("still offers tiers", () => {
    // The fix must not leave the picker empty — a settings control with no
    // options reads as broken, and somebody re-adds the models to fill it.
    expect(MODAL).toContain("Tiers (auto-routing)");
    expect(MODAL).toContain("/api/settings/llm");
  });

  it("keeps an already-saved value visible, and marks it", () => {
    // ⚠️ Part one stops NEW model ids. It does not heal one already stored.
    // Hiding it would leave somebody staring at a picker that disagrees with
    // what their tasks actually run on — so it shows, labelled.
    expect(MODAL).toContain("not a tier");
  });
});
