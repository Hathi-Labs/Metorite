// The readiness matrix — WS-31, the operator UX rebuild.
//
// ⚠️ **This app has no React renderer in its suite**, so anything expressed in
// JSX is untested by construction. That is exactly why every judgement the
// matrix makes is a pure function: the pixels are unverifiable here, the
// MEANING is not, and the meaning is what an operator acts on.
//
// The subject is what an operator CONCLUDES from a cell. Two wrong conclusions
// cost real money, and they are the same mistake in opposite directions:
//
//   1. "this tier works" when the bound model cannot serve the task — a 500 on
//      the first request a customer makes;
//   2. "this tier is fine" when it is servable but unpriced — it runs, and
//      bills nothing.

import { describe, expect, it } from "vitest";

import {
  type CatalogLike,
  buildMatrix,
  cellState,
  capableModelsFor,
  providerOf,
  readinessLine,
  tiersIn,
} from "./readiness";

const CAP = new Set(["gpt-4o::chat"]);
const PRICED = new Map([["gpt-4o::chat", "priced"]]);

const DATA = (over: Partial<CatalogLike> = {}): CatalogLike => ({
  tasks: [
    { slug: "chat", label: "Chat", natural_unit: "tokens" },
    { slug: "transcribe", label: "Transcribe", natural_unit: "minutes" },
  ],
  capabilities: [{ model: "gpt-4o", task: "chat" }],
  bindings: [{ tier: "tier-fast", task: "chat", model: "gpt-4o" }],
  rates: [{ model: "gpt-4o", task: "chat", pricing_mode: "priced" }],
  ...over,
});

describe("one cell, which is the whole judgement", () => {
  it("is empty when no tier points at the pair", () => {
    expect(cellState(null, "chat", CAP, PRICED)).toBe("empty");
  });

  it("is BROKEN when bound to a model that cannot serve the task", () => {
    // 🔴 The Router resolves this model and then cannot decide which provider
    // verb to call. It is the only state here that is actually failing.
    expect(cellState("whisper-1", "chat", CAP, PRICED)).toBe("broken");
  });

  it("is unpriced when capable but the card says so", () => {
    const priced = new Map([["gpt-4o::chat", "unpriced"]]);
    expect(cellState("gpt-4o", "chat", CAP, priced)).toBe("unpriced");
  });

  it("treats a MISSING rate row as unpriced, not as ready", () => {
    // ⚠️ To an operator these are one fact: nobody has said what this costs.
    // Reading "no row" as ready is how a tier ships billing nothing.
    expect(cellState("gpt-4o", "chat", CAP, new Map())).toBe("unpriced");
  });

  it("counts `absorbed` as ready, because that is a DECISION", () => {
    // D19.2's embeddings are deliberately free. Drawing "free on purpose" the
    // same as "nobody has set this yet" is the confusion `describeRate`
    // already exists to prevent in words.
    const priced = new Map([["gpt-4o::chat", "absorbed"]]);
    expect(cellState("gpt-4o", "chat", CAP, priced)).toBe("ready");
  });

  it("🔴 ranks BROKEN above unpriced when a pair is both", () => {
    // The precedence IS the design. A pair that cannot be served must not read
    // as merely a pricing chore somebody can get to next week.
    expect(cellState("whisper-1", "chat", CAP, new Map())).toBe("broken");
  });
});

describe("the tier axis", () => {
  it("is derived from the bindings, never hardcoded", () => {
    // ⚠️ There is no tier registry to read. A fixed list would hide every tier
    // an operator creates, which is the entire set this page is for.
    expect(
      tiersIn([{ tier: "tier-smart" }, { tier: "tier-fast" }, { tier: "tier-fast" }]),
    ).toEqual(["tier-fast", "tier-smart"]);
  });

  it("survives a blank tier without inventing a row", () => {
    expect(tiersIn([{ tier: "" }, { tier: "tier-fast" }])).toEqual(["tier-fast"]);
  });
});

describe("the grid", () => {
  it("gives every tier a cell for every task", () => {
    const m = buildMatrix(DATA());
    expect(m.rows).toHaveLength(1);
    expect(m.rows[0].cells.map((c) => c.task)).toEqual(["chat", "transcribe"]);
  });

  it("counts each state once", () => {
    const m = buildMatrix(DATA());
    expect(m.counts).toEqual({ ready: 1, broken: 0, unpriced: 0, empty: 1 });
  });

  it("lets the LAST binding win for a pair", () => {
    // Bindings are INSERT-only (§6A.5), so the table keeps superseded rows.
    // The Console returns the in-force set today; this stays correct if that
    // ever changes rather than drawing whichever row happened to sort first.
    const m = buildMatrix(
      DATA({
        bindings: [
          { tier: "tier-fast", task: "chat", model: "old-model" },
          { tier: "tier-fast", task: "chat", model: "gpt-4o" },
        ],
      }),
    );
    expect(m.rows[0].cells[0].model).toBe("gpt-4o");
    expect(m.rows[0].cells[0].state).toBe("ready");
  });
});

describe("which vendor a model belongs to", () => {
  it("splits on the FIRST slash", () => {
    expect(providerOf("openai/gpt-4o")).toBe("openai");
  });

  it("🔴 attributes a re-hosted model to the HOST, not the author", () => {
    // ⚠️ `openrouter/anthropic/claude-3` is one OpenRouter model. Splitting on
    // the last slash calls it Anthropic, and the chip then claims a credential
    // we may not hold — which is a wrong answer to "can we serve this".
    expect(providerOf("openrouter/anthropic/claude-3")).toBe("openrouter");
  });

  it("treats a bare id as its own vendor rather than guessing", () => {
    expect(providerOf("whisper-1")).toBe("whisper-1");
    expect(providerOf("")).toBe("");
  });
});

describe("the models a tier may be pointed at", () => {
  it("offers only models that DECLARE the task", () => {
    // 🔴 The whole point of the dropdown. The old form was free text, so an
    // operator could bind a tier to any string at all — and a model with no
    // capability 500s on the first request instead of failing validation.
    const caps = [
      { model: "gpt-4o", task: "chat" },
      { model: "whisper-1", task: "transcribe" },
    ];
    expect(capableModelsFor(caps, "chat")).toEqual(["gpt-4o"]);
  });

  it("de-duplicates and sorts, so the list reads the same every time", () => {
    const caps = [
      { model: "b", task: "chat" },
      { model: "a", task: "chat" },
      { model: "a", task: "chat" },
    ];
    expect(capableModelsFor(caps, "chat")).toEqual(["a", "b"]);
  });

  it("returns empty rather than everything when nothing is capable", () => {
    // ⚠️ Falling back to "all models" would put the broken state back within
    // one click, which is exactly what this list exists to prevent.
    expect(capableModelsFor([{ model: "a", task: "chat" }], "image")).toEqual([]);
  });
});

describe("the line an operator reads first", () => {
  it("says nothing can be served when no tier is bound", () => {
    // 🔴 The shipped state. An empty table reads as a page nobody has used
    // yet; this must read as a system that cannot serve anyone.
    const line = readinessLine(buildMatrix(DATA({ bindings: [] })));
    expect(line).toContain("no AI request can be served");
  });

  it("leads with BROKEN when anything is broken", () => {
    const m = buildMatrix(
      DATA({ bindings: [{ tier: "t", task: "chat", model: "whisper-1" }] }),
    );
    expect(readinessLine(m)).toContain("500");
  });

  it("reports unpriced only once nothing is broken", () => {
    const m = buildMatrix(DATA({ rates: [] }));
    expect(readinessLine(m)).toContain("bill nothing");
  });

  it("reads as a count when everything is fine", () => {
    expect(readinessLine(buildMatrix(DATA()))).toContain("servable and priced");
  });
});
