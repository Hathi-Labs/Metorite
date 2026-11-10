// Drift fence (WS-63 PZ-27): the TS table of interview areas equals `AREAS`
// in the Python package, in ids, order and starter questions. The states too.
//
// Mutation: drop one entry of INTERVIEW_AREAS, and the ids test goes red.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { INTERVIEW_AREAS, INTERVIEW_STATES } from "./profile";

const PY = readFileSync(
  new URL("../../../../../../../packages/acb_common/acb_common/assistant_profile.py", import.meta.url),
  "utf-8",
).replace(/\r\n/g, "\n");

function parseAreas(): [string, string][] {
  const m = /^AREAS: dict\[str, str\] = \{\n([\s\S]*?)^\}/m.exec(PY);
  if (!m) throw new Error("AREAS not found");
  return [...m[1].matchAll(/^\s*"([a-z_]+)":\s*"((?:[^"\\]|\\.)*)",?\s*$/gm)].map((x) => [x[1], x[2]]);
}

describe("the interview areas match the Python table", () => {
  it("the ids, their order and the starter questions are equal", () => {
    const py = parseAreas();
    expect(py.length).toBeGreaterThan(0);
    expect(INTERVIEW_AREAS.map((a) => a.id)).toEqual(py.map((p) => p[0]));
    expect(INTERVIEW_AREAS.map((a) => a.question)).toEqual(py.map((p) => p[1]));
  });
  it("the states are equal", () => {
    const m = /^INTERVIEW_STATES: tuple\[str, \.\.\.\] = \(([^)]*)\)/m.exec(PY);
    const py = [...(m?.[1] ?? "").matchAll(/"([a-z]+)"/g)].map((x) => x[1]);
    expect([...INTERVIEW_STATES]).toEqual(py);
  });
});
