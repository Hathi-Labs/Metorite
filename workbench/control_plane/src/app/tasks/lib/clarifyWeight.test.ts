/**
 * The clarify card and the SHARED priority flags (D78, review 2026-09-24).
 *
 * Since D78, Important and Leveraged are one answer on the task. The clarify
 * proposal is a keyword guess from the title. Before this fence, a member who
 * accepted a proposal as it stood wrote `importance: 0` over a PM's
 * Important, for everyone on the task.
 */
import { describe, expect, it } from "vitest";

import { seedWeight, weightPatch } from "./clarify";

describe("seedWeight — the card starts from the task's own answer", () => {
  it("keeps a teammate's Important over a guess of not-important", () => {
    expect(seedWeight({ important: true, leveraged: true }, {})).toEqual({
      important: true,
      leveraged: true,
    });
  });

  it("keeps a judged not-important over a guess of important", () => {
    expect(seedWeight({ important: false }, { important: true }).important).toBe(false);
  });

  it("lets the guess fill Important only while nobody has judged it", () => {
    expect(seedWeight({}, { important: true }).important).toBe(true);
    expect(seedWeight({}, {}).important).toBe(false);
  });

  it("lets a guess add Leveraged, and never clear it", () => {
    expect(seedWeight({ leveraged: false }, { leveraged: true }).leveraged).toBe(true);
    expect(seedWeight({ leveraged: true }, { leveraged: false }).leveraged).toBe(true);
  });
});

describe("weightPatch — only what the member changed goes to the task", () => {
  it("sends Deep work alone when the shared flags did not change", () => {
    expect(
      weightPatch(
        { important: true, leveraged: false },
        { important: true, leveraged: false, deepWork: true },
      ),
    ).toEqual({ deep_work: true });
  });

  it("sends a flag the member flipped", () => {
    expect(
      weightPatch(
        { important: true, leveraged: false },
        { important: false, leveraged: true, deepWork: false },
      ),
    ).toEqual({ deep_work: false, important: false, leveraged: true });
  });

  it("judges an unjudged task, because clarifying is a judgement", () => {
    expect(
      weightPatch({ leveraged: false }, { important: false, leveraged: false, deepWork: false }),
    ).toEqual({ deep_work: false, important: false });
  });
});
