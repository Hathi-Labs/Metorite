/**
 * The pure rules of WS-45 S3 (D90): the picker plan, the stored choice and
 * the tier label. Spec: `project-docs/specs/ai_tier_routing.md` §7, §8, §9, S3.
 *
 * `AgentChat.picker.test.ts` holds the composer and the answer that use them.
 *
 * Mutations this file catches (R7), each run red before the change:
 *
 * - the plan arms on the UI flag alone, or on a missing `tier_routed`
 *   -> "a covered agent needs both flags";
 * - the plan draws the picker before the agent list lands -> "no picker
 *   flashes while the list loads";
 * - `chatModelField(null)` still sends a field -> "null sends no field";
 * - the usage counts go while an agent still has a picker -> "the usage
 *   counts stay while any agent keeps a picker";
 * - the label repeats a tier, drops its order, or shows an unknown slug
 *   -> the `tierRouteLabel` cases;
 * - a word drifts from the `tier_catalog` seed -> "the words are the seed's";
 * - a covered agent keeps `restoredFor`, so a switch A -> covered B -> A writes
 *   "auto" over A's choice (review P2 of PR #678) -> "a trip through a covered
 *   agent keeps A's stored choice".
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  MODEL_USAGE_KEY,
  ROUTE_EVENT,
  TIER_WORDS,
  agentTierRouted,
  chatModelField,
  composerModelPlan,
  forgetModelChoice,
  getLastModel,
  modelMemoryStep,
  modelPrefKey,
  routeTiers,
  staleModelKeys,
  tierRouteLabel,
  tierRoutingUiOn,
  type ModelStore,
} from "./tierRouting";

function memoryStore(seed: Record<string, string> = {}): ModelStore & { data: Map<string, string>; reads: string[] } {
  const data = new Map(Object.entries(seed));
  const reads: string[] = [];
  return {
    data,
    reads,
    getItem: (k) => {
      reads.push(k);
      return data.has(k) ? data.get(k)! : null;
    },
    setItem: (k, v) => void data.set(k, String(v)),
    removeItem: (k) => void data.delete(k),
  };
}

const route = (tier: string, request: number) => ({
  name: ROUTE_EVENT,
  value: { tier, kind: "chat", reason: "default", request },
});

describe("the UI flag", () => {
  it("ships off, and reads only the on words", () => {
    expect(tierRoutingUiOn({})).toBe(false);
    expect(tierRoutingUiOn({ NEXT_PUBLIC_AI_TIER_ROUTING: "" })).toBe(false);
    expect(tierRoutingUiOn({ NEXT_PUBLIC_AI_TIER_ROUTING: "0" })).toBe(false);
    expect(tierRoutingUiOn({ NEXT_PUBLIC_AI_TIER_ROUTING: "false" })).toBe(false);
    for (const on of ["1", "true", "on", "yes", " TRUE "]) {
      expect(tierRoutingUiOn({ NEXT_PUBLIC_AI_TIER_ROUTING: on })).toBe(true);
    }
  });

  it("reads the literal that Next inlines", () => {
    const src = readFileSync(join(__dirname, "tierRouting.ts"), "utf-8");
    expect(src).toContain("process.env.NEXT_PUBLIC_AI_TIER_ROUTING");
  });
});

describe("composerModelPlan", () => {
  const covered = { name: "projects-assistant", tier_routed: true };
  const plain = { name: "email-assistant", tier_routed: false };
  const today = { covered: false, showPicker: true, fetchModels: true, rememberModel: true, sendModel: true };

  it("with the UI flag off, changes nothing for any agent", () => {
    for (const entry of [covered, plain, undefined]) {
      for (const agentsKnown of [true, false]) {
        expect(composerModelPlan({ uiOn: false, agentsKnown, entry })).toEqual(today);
      }
    }
  });

  it("a covered agent needs both flags", () => {
    expect(composerModelPlan({ uiOn: true, agentsKnown: true, entry: covered })).toEqual({
      covered: true, showPicker: false, fetchModels: false, rememberModel: false, sendModel: false,
    });
    // The UI flag on, and the gateway covers nothing: today's chat.
    expect(composerModelPlan({ uiOn: true, agentsKnown: true, entry: plain })).toEqual(today);
    // An older gateway sends no field. That is not covered.
    expect(composerModelPlan({ uiOn: true, agentsKnown: true, entry: { name: "x" } })).toEqual(today);
    expect(agentTierRouted({ name: "x", tier_routed: "true" as unknown as boolean })).toBe(false);
  });

  it("no picker flashes while the list loads, and the model still goes", () => {
    expect(composerModelPlan({ uiOn: true, agentsKnown: false, entry: undefined })).toEqual({
      covered: false, showPicker: false, fetchModels: false, rememberModel: false, sendModel: true,
    });
  });
});

describe("chatModelField", () => {
  it("null sends no field", () => {
    expect(chatModelField(null)).toEqual({});
    expect(JSON.stringify({ mode: "copilot", ...chatModelField(null), thinkMode: "max" })).toBe(
      '{"mode":"copilot","thinkMode":"max"}',
    );
  });

  it("any other value sends today's field, in today's place", () => {
    expect(chatModelField("tier-powerful")).toEqual({ model: "tier-powerful" });
    expect(chatModelField(undefined)).toEqual({ model: "auto" });
    // The body `useAgentChat` built before S3, for a model the picker chose.
    const before = JSON.stringify({ mode: "copilot", model: "x", thinkMode: "auto" });
    expect(JSON.stringify({ mode: "copilot", ...chatModelField("x"), thinkMode: "auto" })).toBe(before);
  });
});

describe("the stored choice", () => {
  it("keeps today's key names", () => {
    expect(modelPrefKey("projects-assistant")).toBe("cc-model-projects-assistant");
    expect(MODEL_USAGE_KEY).toBe("cc-model-usage");
  });

  it("a covered agent's choice goes", () => {
    const s = memoryStore({ "cc-model-projects-assistant": "tier-powerful", "cc-model-usage": "{}" });
    forgetModelChoice("projects-assistant", [{ name: "projects-assistant", tier_routed: true }, { name: "crm" }], s);
    expect([...s.data.keys()]).toEqual(["cc-model-usage"]);
    expect(getLastModel("projects-assistant", s)).toBeNull();
  });

  it("the usage counts stay while any agent keeps a picker", () => {
    expect(staleModelKeys("a", [{ name: "a", tier_routed: true }, { name: "b" }])).toEqual(["cc-model-a"]);
    expect(staleModelKeys("a", [{ name: "a", tier_routed: true }, { name: "b", tier_routed: true }])).toEqual([
      "cc-model-a",
      "cc-model-usage",
    ]);
    expect(staleModelKeys("a", [])).toEqual(["cc-model-a"]);
  });

  it("a stored value the picker no longer serves does not throw", () => {
    const s = memoryStore({ "cc-model-projects-assistant": "tier-powerful" });
    expect(getLastModel("projects-assistant", s)).toBe("tier-powerful");
    const broken: ModelStore = {
      getItem: () => { throw new Error("denied"); },
      setItem: () => { throw new Error("denied"); },
      removeItem: () => { throw new Error("denied"); },
    };
    expect(getLastModel("a", broken)).toBeNull();
    expect(() => forgetModelChoice("a", [], broken)).not.toThrow();
  });
});

describe("tierRouteLabel", () => {
  it("names both tiers of an answer with two events (done-when 4)", () => {
    expect(tierRouteLabel([route("tier-balanced", 1), route("tier-powerful", 2)])).toBe("Balanced, then Powerful");
  });

  it("names each tier once, in first-use order", () => {
    const events = [route("tier-balanced", 1), route("tier-powerful", 2), route("tier-balanced", 3)];
    expect(routeTiers(events)).toEqual(["Balanced", "Powerful"]);
    expect(tierRouteLabel([route("tier-powerful", 1), route("tier-powerful", 2)])).toBe("Powerful");
  });

  it("shows no slug, no model and no other event", () => {
    expect(tierRouteLabel([route("tier-code", 1)])).toBeNull();
    expect(tierRouteLabel([route("deepseek/deepseek-v4-pro", 1)])).toBeNull();
    expect(tierRouteLabel([{ name: "artifact_created", value: { tier: "tier-fast" } }])).toBeNull();
    expect(tierRouteLabel([{ name: ROUTE_EVENT, value: null }])).toBeNull();
    expect(tierRouteLabel(undefined)).toBeNull();
    expect(tierRouteLabel([route("tier-code", 1), route("tier-fast", 2)])).toBe("Fast");
  });

  it("reads the event name that the executor emits", () => {
    const py = readFileSync(join(__dirname, "../../../../packages/acb_skills/acb_skills/tier_policy.py"), "utf-8");
    expect(py).toContain(`ROUTE_EVENT = "${ROUTE_EVENT}"`);
  });

  it("the words are the seed's (D-AI-1)", () => {
    // `tier_catalog` owns the words. This map mirrors its seed rows until
    // the Control Plane reads `GET /my/tiers`, and fails if the two drift.
    const seed = readFileSync(
      join(__dirname, "../../../../infra/customer_console/015_tier_pricing.sql"),
      "utf-8",
    );
    for (const [slug, word] of Object.entries(TIER_WORDS)) {
      const row = new RegExp(`\\('${slug}',\\s*'([^']+)'`).exec(seed);
      expect(row, slug).not.toBeNull();
      expect(row![1].trim(), slug).toBe(word);
    }
    expect(Object.keys(TIER_WORDS).sort()).toEqual(["tier-balanced", "tier-fast", "tier-powerful"]);
  });
});

describe("modelMemoryStep, the composer's persist effect", () => {
  /**
   * Drives the effect the way React runs it: once per render, and again
   * after a `restore` changes the model. A switch of agent sets the model to
   * "auto" first, as `handleSwitchAgent` does with the UI flag on.
   */
  function composer(store: ModelStore, covered: Set<string>) {
    let agent = "";
    let model = "auto";
    let restoredFor: string | null = null;
    const settle = () => {
      for (let i = 0; i < 3; i++) {
        const step = modelMemoryStep({
          forced: false,
          remember: !covered.has(agent),
          tierUi: true,
          agent,
          model,
          restoredFor,
          readStored: () => getLastModel(agent, store),
        });
        restoredFor = step.restoredFor;
        if (step.kind === "skip") return;
        if (step.kind === "restore") {
          model = step.model;
          continue;
        }
        store.setItem(modelPrefKey(agent), model);
        return;
      }
    };
    return {
      open(name: string) {
        agent = name;
        model = "auto";
        settle();
      },
      get model() {
        return model;
      },
    };
  }

  it("a trip through a covered agent keeps A's stored choice", () => {
    const store = memoryStore({ "cc-model-a": "tier-powerful" });
    const chat = composer(store, new Set(["b"]));
    chat.open("a");
    expect(chat.model).toBe("tier-powerful");
    chat.open("b");
    expect(chat.model).toBe("auto");
    expect(store.data.has("cc-model-b")).toBe(false);
    chat.open("a");
    expect(chat.model).toBe("tier-powerful");
    expect(store.data.get("cc-model-a")).toBe("tier-powerful");
  });

  it("with the UI flag off, persists as before and never restores", () => {
    const step = modelMemoryStep({
      forced: false, remember: true, tierUi: false, agent: "a", model: "x",
      restoredFor: null, readStored: () => "y",
    });
    expect(step).toEqual({ kind: "persist", restoredFor: null });
  });

  it("a forced model writes nothing", () => {
    const step = modelMemoryStep({
      forced: true, remember: true, tierUi: true, agent: "a", model: "x",
      restoredFor: "a", readStored: () => "y",
    });
    expect(step.kind).toBe("skip");
  });
});
