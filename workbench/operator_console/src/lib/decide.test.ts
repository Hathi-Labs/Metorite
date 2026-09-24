// "Try a decision" (CP-13b, §6A.14). The form rules, the read of the
// Console's answer, and the panel that draws it.
//
// ⚠️ vitest here is node-env with no DOM. `DecisionResult` has no hooks, so
// the case calls it and walks the element tree it returns, as
// `login.test.ts` does.

import { describe, expect, it } from "vitest";

import DecisionResult from "@/app/tiers/DecisionResult";
import {
  answerText,
  costText,
  meteringWarning,
  readTryResult,
  tryBlocker,
  tryBody,
  type TryDraft,
} from "./decide";

function text(node: unknown): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  const el = node as { props?: { children?: unknown } };
  return el.props && "children" in el.props ? text(el.props.children) : "";
}

const DRAFT: TryDraft = {
  state: "Sixteen pumps are overdue.",
  type: "choice",
  instructions: "Which rule fits?",
  criteria: [
    { key: "fyi", text: "read only" },
    { key: "needs_reply", text: "a reply" },
    { key: "", text: "a row with no key is dropped" },
  ],
};

//: The Console's answer, in the shape `POST /catalog/decide/try` returns.
const ANSWER = {
  tier: "tier-decide",
  model: "typesafe/jev-1.13.0",
  answers: { q: { type: "choice", choice: "fyi", probabilities: {}, confidence: 0.81 } },
  usage: { input_tokens: 312, output_tokens: 4 },
  latency_ms: 142,
  vendor_cost_usd: "0.00001310",
};

describe("the form", () => {
  it("sends one question, and drops a criterion with no key", () => {
    expect(tryBody(DRAFT)).toEqual({
      state: "Sixteen pumps are overdue.",
      questions: {
        q: {
          type: "choice",
          instructions: "Which rule fits?",
          criteria: { fyi: "read only", needs_reply: "a reply" },
        },
      },
    });
  });

  it("says what is missing before it lets the operator send", () => {
    expect(tryBlocker(DRAFT)).toBeNull();
    expect(tryBlocker({ ...DRAFT, state: " " })).toMatch(/state/);
    expect(tryBlocker({ ...DRAFT, criteria: [{ key: "a", text: "" }] })).toMatch(/two or more/);
    expect(
      tryBlocker({ ...DRAFT, type: "score", criteria: [{ key: "a", text: "" }] }),
    ).toMatch(/2 to 10/);
    // A yes-or-no question needs no criteria at all.
    expect(tryBlocker({ ...DRAFT, type: "boolean", criteria: [] })).toBeNull();
  });
});

describe("the answer", () => {
  it("reads each of the three types in words", () => {
    expect(answerText({ type: "boolean", probability: 0.93 })).toBe(
      "yes, with probability 93%",
    );
    expect(answerText({ type: "choice", choice: "fyi", confidence: 0.81 })).toBe(
      "fyi (confidence 81%)",
    );
    expect(answerText({ type: "score", score: "high", confidence: null })).toBe("high");
    // CP-13h: the level, then the fractional position.
    expect(
      answerText({ type: "score", score: 1.3, level: "frustrated", confidence: 0.55 }),
    ).toBe("frustrated (position 1.3, confidence 55%)");
    expect(answerText(undefined)).toBe("no readable answer");
  });

  it("reads the Console's body, and refuses a body with no answers", () => {
    expect(readTryResult(ANSWER)).toEqual({
      tier: "tier-decide",
      model: "typesafe/jev-1.13.0",
      answer: "fyi (confidence 81%)",
      inputTokens: 312,
      outputTokens: 4,
      latencyMs: 142,
      vendorCostUsd: "0.00001310",
    });
    expect(readTryResult({ detail: "nope" })).toBeNull();
  });

  it("says 'not priced' rather than a zero when the profile holds no price", () => {
    expect(costText(null)).toBe("not priced");
    expect(costText("0.00001310")).toBe("$0.00001310");
  });

  it("🔴 flags zero input tokens as the metering fault, not a success", () => {
    const r = readTryResult({ ...ANSWER, usage: { input_tokens: 0, output_tokens: 0 } });
    expect(r && meteringWarning(r)).toMatch(/zero input tokens/);
  });

  it("the panel draws the answer, the latency, both token counts and the cost", () => {
    const r = readTryResult(ANSWER);
    expect(r).not.toBeNull();
    const shown = text(DecisionResult({ result: r! }));
    expect(shown).toContain("fyi (confidence 81%)");
    expect(shown).toContain("142 ms");
    expect(shown).toContain("312");
    expect(shown).toContain("$0.00001310");
    expect(shown).toContain("typesafe/jev-1.13.0");
    expect(shown).not.toContain("zero input tokens");
  });
});
