// D32.7 — the customer never sees a model. Closes H-72 part one, and /email.
//
// ⚠️ **This was live, not theoretical.** `/tasks` is one of the nine live
// panes, `page.tsx` renders `<TaskSettingsModal />` twice, and the modal
// offered every enabled model by raw id under a "your enabled models"
// optgroup. The chosen value reached `AssistantRail`'s chat call.
//
// 🔴 **The break was silent until the flag flip.** The defaults are tiers, so
// nothing looked wrong — but a customer who deliberately picked a model stored
// a bare model id, and the Console refuses one with a 400 rather than coercing
// it. That customer loses their AI the day `ROUTER_SERVING_ENABLED` flips, and
// they are the customer most engaged with the product.
//
// ⚠️ **ONE fence, a TABLE of surfaces — deliberately.** This started as a
// `/tasks`-only file. `/email` carried the identical defect, and a second
// copy of this test would have been a second place to remember. When a third
// surface grows a model picker, add a row; do not add a file.

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const APP = join(__dirname, "..", "app");

/** Every surface that lets a person choose what their AI runs on. */
const SURFACES = [
  {
    name: "/tasks (LIVE — one of the nine)",
    path: join(APP, "tasks", "components", "TaskSettingsModal.tsx"),
  },
  {
    name: "/email (preview — WS-17)",
    path: join(
      APP, "email", "components", "automation", "ai-settings", "SettingsTab.tsx",
    ),
  },
] as const;

describe.each(SURFACES)("$name offers tiers only", ({ path }) => {
  const src = readFileSync(path, "utf8");

  it("has no raw-model optgroup", () => {
    // ⚠️ The comment that REPLACES an optgroup must not quote its label
    // either. This check has gone red on its own explanation twice now —
    // once in each surface — and it is the fifth and sixth time in this repo
    // that a source fence matched the prose forbidding the thing. Text
    // fences are cheap and right for JSX, but the prose must stay clear.
    expect(src).not.toContain("Your enabled models");
    expect(src).not.toContain("enabledModels");
  });

  it("does not read the enabled-model catalogue at all", () => {
    // ⚠️ A read kept alive for a surface that no longer exists is how the
    // surface quietly comes back. The endpoint still serves the operator
    // console; these components must not use it.
    expect(src).not.toContain("/api/settings/llm/enabled-models");
  });

  it("still offers tiers", () => {
    // The fix must not leave the picker empty — a settings control with no
    // options reads as broken, and somebody re-adds the models to fill it.
    expect(src).toContain("/api/settings/llm");
    expect(src).toMatch(/tiers/i);
  });

  it("keeps an already-saved value visible, and marks it", () => {
    // ⚠️ This stops NEW model ids. It does not heal one already stored.
    // Hiding it would leave somebody staring at a picker that disagrees with
    // what their AI actually runs on — so it shows, labelled.
    expect(src).toContain("not a tier");
  });
});

describe("the fence covers every picker that exists", () => {
  it("names both known surfaces", () => {
    // A table-driven fence silently shrinks if a row is deleted. Pinning the
    // count means removing a surface is a decision somebody makes on purpose.
    expect(SURFACES).toHaveLength(2);
  });
});
